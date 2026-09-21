"""Consistent SQLite snapshots and non-overwriting restore verification.

This backs up engine state only, not app secrets/configuration or media files.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_database(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('DATABASE_INTEGRITY_FAILED')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('DATABASE_FOREIGN_KEYS_FAILED')
        versions = db.execute('SELECT version_num FROM alembic_version').fetchall()
        if len(versions) != 1:
            raise ValueError('INVALID_SCHEMA_VERSION')
        return versions[0][0]


def copy_database(source, target):
    # sqlite backup includes committed WAL pages, unlike copying engine.db alone.
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            dst.execute('PRAGMA journal_mode=DELETE')
    os.chmod(target, 0o600)
    with target.open('rb') as handle:
        os.fsync(handle.fileno())


def backup(state, destination):
    source = state / 'engine.db'
    if source.is_symlink() or not source.is_file():
        raise ValueError('INVALID_SOURCE_DATABASE')
    if destination.exists() or destination.is_symlink():
        raise ValueError('DESTINATION_ALREADY_EXISTS')
    destination.mkdir(mode=0o700)  # Exclusive ownership; never clobber an existing backup.
    target = destination / 'engine.db'
    copy_database(source, target)
    manifest = {'format': 1, 'sha256': digest(target), 'schema': validate_database(target),
                'scope': 'engine_database_only'}
    with (destination / 'manifest.json').open('x') as handle:
        os.chmod(handle.name, 0o600)
        json.dump(manifest, handle)
        handle.flush()
        os.fsync(handle.fileno())
    sync_directory(destination)
    sync_directory(destination.parent)
    return manifest


def restore_new(snapshot, destination):
    source = snapshot / 'engine.db'
    manifest_path = snapshot / 'manifest.json'
    if source.is_symlink() or manifest_path.is_symlink():
        raise ValueError('BACKUP_SYMLINK_NOT_ALLOWED')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('format') != 1 or manifest.get('scope') != 'engine_database_only':
        raise ValueError('UNSUPPORTED_BACKUP_FORMAT')
    if digest(source) != manifest.get('sha256'):
        raise ValueError('BACKUP_CHECKSUM_MISMATCH')
    if destination.exists() or destination.is_symlink():
        raise ValueError('DESTINATION_ALREADY_EXISTS')
    # Stage and verify before making the requested directory visible.
    with tempfile.TemporaryDirectory(prefix='.aiarr-restore-', dir=destination.parent) as stage:
        target = Path(stage) / 'engine.db'
        copy_database(source, target)
        if validate_database(target) != manifest.get('schema'):
            raise ValueError('BACKUP_SCHEMA_MISMATCH')
        destination.mkdir(mode=0o700)  # Atomic exclusive mkdir also prevents races.
        os.rename(target, destination / 'engine.db')
        sync_directory(destination)
        sync_directory(destination.parent)
    return {'schema': manifest['schema'], 'scope': 'engine_database_only'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'restore-new'])
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        action = backup if args.operation == 'backup' else restore_new
        result = action(args.source, args.destination)
        print(json.dumps({'result': 'PASS', **result}))
    except Exception as exc:
        # Avoid emitting database values or private filesystem paths on errors.
        print(json.dumps({'result': 'FAIL', 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
