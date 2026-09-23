import argparse
import logging
import time

import uvicorn
from alembic import command
from alembic.config import Config

from mae.config import Settings
from mae.db import Database
from mae.runtime_config import RuntimeConfig
from mae.seerr import Seerr
from mae.worker import Worker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "api", "worker", "once", "reconcile"])
    args = parser.parse_args()
    settings = Settings()
    if args.command == "migrate":
        command.upgrade(Config("alembic.ini"), "head")
        return
    db = Database(settings.state_dir)
    db.execute("SELECT id FROM jobs LIMIT 1")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # HTTP libraries log full URLs at INFO; source URLs may contain private passkeys.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.command == "api":
        from mae.api import create_app

        uvicorn.run(create_app(settings, db), host="0.0.0.0", port=8080, access_log=False)
    elif args.command == "reconcile":
        current = RuntimeConfig(settings).load()
        Seerr(current).reconcile(db, current.dry_run)
    else:
        store = RuntimeConfig(settings)
        current = store.load()
        worker = Worker(current, db)
        last_reconcile = 0
        while True:
            latest = store.load()
            if latest != current:
                current = latest
                worker = Worker(current, db)
                last_reconcile = 0
                logging.info('{"event":"runtime_config_reloaded"}')
            if current.seerr_key.get_secret_value() and time.monotonic() - last_reconcile > 60:
                try:
                    Seerr(current).reconcile(db, current.dry_run)
                except Exception as exc:
                    logging.warning('{"event":"reconciliation_failed","type":"%s"}', type(exc).__name__)
                last_reconcile = time.monotonic()
            worker.tick()
            if args.command == "once":
                break
            time.sleep(10)
