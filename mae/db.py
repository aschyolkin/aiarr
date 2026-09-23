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

    def set_poster(self, job_id, path):
        self.execute(
            "UPDATE jobs SET work=json_set(work,'$.poster_path',:path) WHERE id=:id "
            "AND json_extract(work,'$.poster_path') IS NULL", id=job_id, path=path,
        )

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

    def mark_seerr_request_deleted(self, request_id):
        """Retire a confirmed 404 request; cleanup is durable and worker-owned."""
        now = time.time()
        with self.engine.begin() as conn:
            request = conn.execute(
                text("SELECT job_id,status FROM seerr_requests WHERE request_id=:id"), {"id": request_id}
            ).mappings().first()
            if request is None or request["status"] == -1:
                return False
            conn.execute(
                text("UPDATE seerr_requests SET status=-1,active=0,suppressed=1,updated=:now "
                     "WHERE request_id=:id"), {"id": request_id, "now": now}
            )
            active = conn.execute(
                text("SELECT 1 FROM seerr_requests WHERE job_id=:id AND active=1 AND suppressed=0 LIMIT 1"),
                {"id": request["job_id"]},
            ).first()
            if active:
                return False
            conn.execute(
                text("UPDATE jobs SET state='CANCELLED',reason='SEERR_REQUEST_DELETED',"
                     "next_run=:now,cancel_pause_done=0 WHERE id=:id"),
                {"id": request["job_id"], "now": now},
            )
            conn.execute(
                text("INSERT INTO events(job_id,state,reason,created) "
                     "VALUES(:id,'CANCELLED','SEERR_REQUEST_DELETED',:now)"),
                {"id": request["job_id"], "now": now},
            )
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

    def downloads_to_pause(self):
        # Cancellations that fully delete the download (admin cancel, a Seerr
        # request confirmed deleted) are handled by downloads_to_delete
        # instead; only a reversible/automatic state retains the torrent.
        return self.execute(
            "SELECT id,torrent_hash FROM jobs WHERE ((state='CANCELLED' "
            "AND reason NOT IN ('SEERR_REQUEST_DELETED','ADMIN_CANCELLED')) OR "
            "(state='NEEDS_REVIEW' AND reason IN "
            "('NO_MONITORED_EPISODES','EPISODE_MONITORING_CHANGED'))) AND torrent_hash IS NOT NULL "
            "AND cancel_pause_done=0 ORDER BY id"
        )

    def downloads_to_delete(self):
        # An admin cancel or a library item that disappeared from Jellyfin
        # both mean this job's download is no longer wanted. Deleting it from
        # qBittorrent is safe even for a partially or fully imported job: an
        # import is a hardlink, a second independent directory entry to the
        # same file, so removing qBittorrent's own copy never touches the
        # published library copy.
        return self.execute(
            "SELECT id,torrent_hash,state FROM jobs WHERE "
            "((state='CANCELLED' AND reason='ADMIN_CANCELLED') OR "
            "(state='NEEDS_REVIEW' AND reason='LIBRARY_ITEM_MISSING')) "
            "AND torrent_hash IS NOT NULL AND cancel_pause_done=0 ORDER BY id"
        )

    def finish_download_delete(self, job_id, state):
        now = time.time()
        with self.engine.begin() as conn:
            changed = conn.execute(
                text("UPDATE jobs SET cancel_pause_done=1,torrent_hash=NULL,reserved=0 "
                     "WHERE id=:id AND cancel_pause_done=0"),
                {"id": job_id},
            )
            if changed.rowcount:
                conn.execute(
                    text("INSERT INTO events(job_id,state,reason,created) "
                         "VALUES(:id,:state,'DOWNLOAD_REMOVED',:now)"),
                    {"id": job_id, "state": state, "now": now},
                )

    def deleted_request_cleanups(self):
        return self.execute(
            "SELECT * FROM jobs WHERE state='CANCELLED' AND reason='SEERR_REQUEST_DELETED' "
            "AND cancel_pause_done=0 AND next_run<=:now ORDER BY id", now=time.time()
        )

    def finish_deleted_request_cleanup(self, job_id):
        now = time.time()
        with self.engine.begin() as conn:
            changed = conn.execute(
                text("UPDATE jobs SET cancel_pause_done=1,torrent_hash=NULL,reserved=0 WHERE id=:id "
                     "AND state='CANCELLED' AND reason='SEERR_REQUEST_DELETED' AND cancel_pause_done=0"),
                {"id": job_id},
            )
            if changed.rowcount:
                conn.execute(
                    text("INSERT INTO events(job_id,state,reason,created) "
                         "VALUES(:id,'CANCELLED','SEERR_CLEANUP_DONE',:now)"), {"id": job_id, "now": now}
                )

    def block_deleted_request_cleanup(self, job_id, code, delay=600):
        """Keep data in place; retry later and record each distinct blocker once."""
        now = time.time()
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE jobs SET next_run=:next WHERE id=:id AND state='CANCELLED' "
                     "AND reason='SEERR_REQUEST_DELETED'"), {"id": job_id, "next": now + delay}
            )
            last = conn.execute(
                text("SELECT reason FROM events WHERE job_id=:id ORDER BY id DESC LIMIT 1"), {"id": job_id}
            ).first()
            if last is None or last[0] != code:
                conn.execute(
                    text("INSERT INTO events(job_id,state,reason,created) VALUES(:id,'CANCELLED',:code,:now)"),
                    {"id": job_id, "code": code, "now": now},
                )

    def mark_pause_done(self, job_id):
        self.execute(
            "UPDATE jobs SET cancel_pause_done=1 WHERE id=:id AND (state='CANCELLED' OR "
            "(state='NEEDS_REVIEW' AND reason IN "
            "('NO_MONITORED_EPISODES','EPISODE_MONITORING_CHANGED')))", id=job_id
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

    def desired_episodes(self, job_id):
        return [r["episode"] for r in self.execute(
            "SELECT episode FROM episodes WHERE job_id=:id AND monitored=1 ORDER BY episode", id=job_id
        )]

    def set_episode_monitoring(self, episode_ids, monitored):
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT id,job_id FROM episodes WHERE id IN (%s)" % ",".join(f":id{i}" for i in range(len(episode_ids)))),
                {f"id{i}": value for i, value in enumerate(episode_ids)},
            ).mappings().all()
            if len(rows) != len(set(episode_ids)):
                raise KeyError("EPISODE_NOT_FOUND")
            for row in rows:
                conn.execute(text("UPDATE episodes SET monitored=:monitored WHERE id=:id"), {"id": row["id"], "monitored": int(monitored)})
            for job_id in {row["job_id"] for row in rows}:
                if monitored:
                    conn.execute(text(
                        "UPDATE jobs SET state='REQUESTED',next_run=0,reason=NULL "
                        "WHERE id=:id AND (state='AVAILABLE' OR "
                        "(state='WAITING_FOR_RELEASE' AND reason='NO_MONITORED_EPISODES'))"
                    ), {"id": job_id})
                else:
                    remaining = conn.execute(text(
                        "SELECT 1 FROM episodes WHERE job_id=:id AND monitored=1 LIMIT 1"
                    ), {"id": job_id}).first()
                    if not remaining:
                        conn.execute(text(
                            "UPDATE jobs SET state=CASE WHEN torrent_hash IS NULL THEN 'WAITING_FOR_RELEASE' "
                            "ELSE 'NEEDS_REVIEW' END,reason='NO_MONITORED_EPISODES',"
                            "next_run=0,cancel_pause_done=0 WHERE id=:id "
                            "AND state NOT IN ('AVAILABLE','CANCELLED','FAILED','NEEDS_REVIEW','AUTH_REQUIRED')"
                        ), {"id": job_id})
                    else:
                        conn.execute(text(
                            "UPDATE jobs SET state='NEEDS_REVIEW',reason='EPISODE_MONITORING_CHANGED',"
                            "next_run=0,cancel_pause_done=0 WHERE id=:id AND torrent_hash IS NOT NULL "
                            "AND state NOT IN ('AVAILABLE','CANCELLED','FAILED','NEEDS_REVIEW','AUTH_REQUIRED')"
                        ), {"id": job_id})

    def observe_completed(self, job, coverage):
        work = json.loads(job["work"])
        with self.engine.begin() as conn:
            wanted = (
                {"movie"}
                if work["kind"] == "movie"
                else {
                    row[0]
                    for row in conn.execute(
                        text("SELECT episode FROM episodes WHERE job_id=:id AND monitored=1"), {"id": job["id"]}
                    )
                }
            )
            missing = bool(wanted) and not wanted.issubset(coverage)
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
