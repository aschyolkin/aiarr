import os

import pytest

from mae.backup import backup, restore_new
from mae.db import Database


def test_wal_snapshot_restore_keeps_jobs_and_does_not_change_source(settings, db, movie, tmp_path):
    job_id = db.submit(movie)
    db.transition(job_id, 'WAITING_FOR_RELEASE', 'FIXTURE')
    before = db.jobs()
    snapshot = tmp_path / 'snapshot'
    manifest = backup(settings.state_dir, snapshot)
    assert manifest['schema'] == '0012'
    destination = tmp_path / 'restored'
    restore_new(snapshot, destination)
    restored = Database(destination)
    assert restored.jobs() == before == db.jobs()
    assert restored.submit(movie) == job_id
    assert len(restored.jobs()) == 1
    assert os.stat(snapshot / 'engine.db').st_mode & 0o777 == 0o600
    assert os.stat(destination).st_mode & 0o777 == 0o700
    restored.engine.dispose()


def test_corrupt_backup_cannot_be_restored(settings, db, tmp_path):
    snapshot = tmp_path / 'snapshot'
    backup(settings.state_dir, snapshot)
    with (snapshot / 'engine.db').open('ab') as handle:
        handle.write(b'corruption')
    destination = tmp_path / 'restored'
    with pytest.raises(ValueError, match='CHECKSUM'):
        restore_new(snapshot, destination)
    assert not destination.exists()


def test_no_backup_or_restore_overwrites_existing_data(settings, db, tmp_path):
    snapshot = tmp_path / 'snapshot'
    backup(settings.state_dir, snapshot)
    with pytest.raises(ValueError, match='ALREADY_EXISTS'):
        backup(settings.state_dir, snapshot)
    destination = tmp_path / 'existing'
    destination.mkdir()
    sentinel = destination / 'original'
    sentinel.write_text('preserve')
    with pytest.raises(ValueError, match='ALREADY_EXISTS'):
        restore_new(snapshot, destination)
    assert sentinel.read_text() == 'preserve'


def test_symlink_database_rejected(settings, db, tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'engine.db').symlink_to(settings.state_dir / 'engine.db')
    with pytest.raises(ValueError, match='INVALID_SOURCE'):
        backup(source, tmp_path / 'snapshot')
