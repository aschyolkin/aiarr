import json
import time

from sqlalchemy import create_engine, event, text


class Database:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine = create_engine(f"sqlite:///{directory / 'engine.db'}", connect_args={"timeout": 30})

        @event.listens_for(self.engine, "connect")
        def configure(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")

    def execute(self, sql, **params):
        with self.engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return [dict(r) for r in result.mappings()] if result.returns_rows else result.rowcount

    def jobs(self):
        return self.execute("SELECT * FROM jobs ORDER BY id")

    def job(self, job_id):
        rows = self.execute("SELECT * FROM jobs WHERE id=:id", id=job_id)
        if not rows:
            raise KeyError(job_id)
        return rows[0]

    def submit(self, work, dry_run=True):
        key = f"{work.kind}:{work.external_id}"
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT OR IGNORE INTO jobs (identity,work,state,dry_run,next_run) "
                    "VALUES (:key,:work,'REQUESTED',:dry,0)"
                ),
                {"key": key, "work": work.model_dump_json(), "dry": int(dry_run)},
            )
            row = conn.execute(text("SELECT * FROM jobs WHERE identity=:key"), {"key": key}).mappings().one()
            if work.kind == "series":
                previous = json.loads(row["work"])
                merged = work.model_copy(
                    update={
                        "seasons": sorted(set(previous["seasons"]) | set(work.seasons)),
                        "episodes": sorted(set(previous["episodes"]) | set(work.episodes)),
                    }
                )
                conn.execute(
                    text("UPDATE jobs SET work=:work WHERE id=:id"),
                    {"work": merged.model_dump_json(), "id": row["id"]},
                )
                for episode in merged.episodes:
                    conn.execute(
                        text("INSERT OR IGNORE INTO episodes(job_id,episode) VALUES(:job,:episode)"),
                        {"job": row["id"], "episode": episode},
                    )
                if set(merged.episodes) - set(previous["episodes"]) and row["state"] == "AVAILABLE":
                    conn.execute(
                        text(
                            "UPDATE jobs SET state='REQUESTED',next_run=0,torrent_hash=NULL,"
                            "candidate=NULL,decision=NULL,reserved=0,attempts=0,reason=NULL WHERE id=:id"
                        ),
                        {"id": row["id"]},
                    )
            return row["id"]

    def record_coverage(self, job_id, coverage):
        # A fresh successful Jellyfin response replaces earlier observations.
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE episodes SET available=0 WHERE job_id=:id"), {"id": job_id})
            for episode in coverage:
                conn.execute(
                    text("UPDATE episodes SET available=1 WHERE job_id=:id AND episode=:episode"),
                    {"id": job_id, "episode": episode},
                )

    def observe_completed(self, job, coverage):
        work = json.loads(job["work"])
        wanted = {"movie"} if work["kind"] == "movie" else set(work["episodes"])
        missing = not wanted or not wanted.issubset(coverage)
        with self.engine.begin() as conn:
            changed = conn.execute(
                text(
                    "UPDATE jobs SET state=:state,reason=:reason,next_run=:next "
                    "WHERE id=:id AND state='AVAILABLE' AND work=:work"
                ),
                {
                    "state": "NEEDS_REVIEW" if missing else "AVAILABLE",
                    "reason": "LIBRARY_ITEM_MISSING" if missing else job["reason"],
                    "next": time.time() + 300,
                    "id": job["id"],
                    "work": job["work"],
                },
            )
            if not changed.rowcount:
                return False  # A new request/cancellation superseded this snapshot.
            conn.execute(text("UPDATE episodes SET available=0 WHERE job_id=:id"), {"id": job["id"]})
            for episode in coverage:
                conn.execute(
                    text("UPDATE episodes SET available=1 WHERE job_id=:id AND episode=:episode"),
                    {"id": job["id"], "episode": episode},
                )
            if missing:
                conn.execute(
                    text(
                        "INSERT INTO events(job_id,state,reason,created) "
                        "VALUES(:id,'NEEDS_REVIEW','LIBRARY_ITEM_MISSING',:now)"
                    ),
                    {"id": job["id"], "now": time.time()},
                )
            return True

    def transition(self, job_id, state, reason=None, delay=0, **updates):
        allowed = {"attempts", "candidate", "torrent_hash", "reserved", "decision", "dry_run"}
        if set(updates) - allowed:
            raise ValueError("unsupported job update")
        params = dict(id=job_id, state=state, reason=reason, next_run=time.time() + delay, **updates)
        assignments = ",".join(f"{key}=:{key}" for key in ("state", "reason", "next_run", *updates))
        with self.engine.begin() as conn:
            changed = conn.execute(
                text(f"UPDATE jobs SET {assignments} WHERE id=:id AND state!='CANCELLED'"), params
            )
            if not changed.rowcount:
                return
            conn.execute(
                text("INSERT INTO events(job_id,state,reason,created) VALUES(:id,:state,:reason,:created)"),
                dict(id=job_id, state=state, reason=reason, created=time.time()),
            )
