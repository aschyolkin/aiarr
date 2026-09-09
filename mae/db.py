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

    def observe_seerr_request(self, request_id, job_id, status):
        """Persist one Seerr request observation and safely re-open a new generation.

        A request absent from a later API page is deliberately not an observation:
        Seerr can hide old requests as availability changes. Only an explicit,
        non-approved status can cancel active acquisition work.
        """
        if type(request_id) is not int or request_id < 1:
            return False
        now = time.time()
        active = int(status == 2)  # MediaRequestStatus.APPROVED in Seerr.
        with self.engine.begin() as conn:
            job = conn.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}).mappings().one()
            existing = conn.execute(
                text("SELECT * FROM seerr_requests WHERE request_id=:id"), {"id": request_id}
            ).mappings().first()
            if existing is not None and existing["job_id"] != job_id:
                raise ValueError("SEERR_REQUEST_ID_COLLISION")
            # A new request, or a later real re-approval of an unsuppressed one,
            # is an explicit re-request after cancellation.
            re_open = active and job["state"] == "CANCELLED" and (
                existing is None or not existing["suppressed"]
            )
            generation = job["generation"] + int(re_open)
            suppressed = int(existing["suppressed"]) if existing is not None else 0
            conn.execute(
                text(
                    "INSERT INTO seerr_requests(request_id,job_id,status,active,suppressed,generation,updated) "
                    "VALUES(:request,:job,:status,:active,:suppressed,:generation,:updated) "
                    "ON CONFLICT(request_id) DO UPDATE SET status=excluded.status,active=excluded.active,"
                    "generation=excluded.generation,updated=excluded.updated"
                ),
                {
                    "request": request_id, "job": job_id, "status": status, "active": active,
                    "suppressed": suppressed, "generation": generation, "updated": now,
                },
            )
            if re_open:
                conn.execute(
                    text(
                        "UPDATE jobs SET state='REQUESTED',reason='SEERR_REREQUESTED',next_run=0,"
                        "torrent_hash=NULL,candidate=NULL,decision=NULL,reserved=0,attempts=0,cancel_pause_done=0,generation=:generation "
                        "WHERE id=:id"
                    ),
                    {"id": job_id, "generation": generation},
                )
                conn.execute(
                    text("INSERT INTO events(job_id,state,reason,created) VALUES(:id,'REQUESTED','SEERR_REREQUESTED',:now)"),
                    {"id": job_id, "now": now},
                )
            self._cancel_if_no_active_seerr_request(conn, job_id, now)
            return re_open

    def cancel_from_admin(self, job_id):
        now = time.time()
        with self.engine.begin() as conn:
            changed = conn.execute(
                text("UPDATE jobs SET state='CANCELLED',reason='ADMIN_CANCELLED',next_run=:now,cancel_pause_done=0 WHERE id=:id AND state!='CANCELLED'"),
                {"id": job_id, "now": now},
            )
            if not changed.rowcount:
                return False
            # An engine-side cancellation remains cancelled while the same Seerr
            # request is approved. A new request ID is required to re-open it.
            conn.execute(text("UPDATE seerr_requests SET suppressed=1,updated=:now WHERE job_id=:id AND active=1"), {"id": job_id, "now": now})
            conn.execute(text("INSERT INTO events(job_id,state,reason,created) VALUES(:id,'CANCELLED','ADMIN_CANCELLED',:now)"), {"id": job_id, "now": now})
            return True

    def _cancel_if_no_active_seerr_request(self, conn, job_id, now):
        active = conn.execute(
            text("SELECT 1 FROM seerr_requests WHERE job_id=:id AND active=1 AND suppressed=0 LIMIT 1"),
            {"id": job_id},
        ).first()
        if active:
            return False
        changed = conn.execute(
            text(
                "UPDATE jobs SET state='CANCELLED',reason='SEERR_REQUEST_NOT_ACTIVE',next_run=:now,cancel_pause_done=0 "
                "WHERE id=:id AND state NOT IN ('AVAILABLE','CANCELLED','FAILED','NEEDS_REVIEW','AUTH_REQUIRED')"
            ),
            {"id": job_id, "now": now},
        )
        if changed.rowcount:
            conn.execute(text("INSERT INTO events(job_id,state,reason,created) VALUES(:id,'CANCELLED','SEERR_REQUEST_NOT_ACTIVE',:now)"), {"id": job_id, "now": now})
        return bool(changed.rowcount)

    def cancelled_downloads_to_pause(self):
        return self.execute(
            "SELECT id,torrent_hash FROM jobs WHERE state='CANCELLED' AND torrent_hash IS NOT NULL "
            "AND cancel_pause_done=0 ORDER BY id"
        )

    def mark_cancel_pause_done(self, job_id):
        self.execute(
            "UPDATE jobs SET cancel_pause_done=1 WHERE id=:id AND state='CANCELLED'", id=job_id
        )

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

    def tags(self):
        return self.execute("SELECT id,label FROM tags ORDER BY label COLLATE NOCASE,id")

    def create_tag(self, label):
        with self.engine.begin() as conn:
            try:
                result = conn.execute(text("INSERT INTO tags(label) VALUES(:label)"), {"label": label})
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ValueError("TAG_ALREADY_EXISTS") from None
                raise
            return {"id": result.lastrowid, "label": label}

    def update_tag(self, tag_id, label):
        with self.engine.begin() as conn:
            try:
                changed = conn.execute(
                    text("UPDATE tags SET label=:label WHERE id=:id"), {"id": tag_id, "label": label}
                )
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ValueError("TAG_ALREADY_EXISTS") from None
                raise
            if not changed.rowcount:
                raise KeyError(tag_id)
            return {"id": tag_id, "label": label}

    def delete_tag(self, tag_id):
        with self.engine.begin() as conn:
            if conn.execute(text("SELECT 1 FROM job_tags WHERE tag_id=:id LIMIT 1"), {"id": tag_id}).first():
                raise ValueError("TAG_IN_USE")
            changed = conn.execute(text("DELETE FROM tags WHERE id=:id"), {"id": tag_id})
            if not changed.rowcount:
                raise KeyError(tag_id)

    def tags_for_job(self, job_id):
        return [
            r["id"]
            for r in self.execute(
                "SELECT tags.id FROM tags JOIN job_tags ON tags.id=job_tags.tag_id "
                "WHERE job_tags.job_id=:id ORDER BY tags.id",
                id=job_id,
            )
        ]

    def set_job_tags(self, job_id, tag_ids):
        if len(tag_ids) != len(set(tag_ids)) or any(type(tag) is not int or tag < 1 for tag in tag_ids):
            raise ValueError("INVALID_TAG_IDS")
        with self.engine.begin() as conn:
            existing = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT id FROM tags WHERE id IN (%s)"
                        % ",".join(f":tag{i}" for i in range(len(tag_ids)))
                    )
                    if tag_ids
                    else text("SELECT id FROM tags WHERE 0"),
                    {f"tag{i}": tag for i, tag in enumerate(tag_ids)},
                )
            }
            if existing != set(tag_ids):
                raise ValueError("UNKNOWN_TAG")
            conn.execute(text("DELETE FROM job_tags WHERE job_id=:id"), {"id": job_id})
            for tag_id in tag_ids:
                conn.execute(
                    text("INSERT INTO job_tags(job_id,tag_id) VALUES(:job,:tag)"),
                    {"job": job_id, "tag": tag_id},
                )

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
