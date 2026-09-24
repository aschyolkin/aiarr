import fcntl
import json
import logging
import re
import shutil
import stat
import time
from pathlib import Path

from guessit import guessit

from mae.db import Database
from mae.deepseek import DeepSeek
from mae.domain import Outcome, ProviderError, Work
from mae.downloads import Downloader, safe_relative, torrent_metadata
from mae.importing import destination_for
from mae.jellyfin import Jellyfin
from mae.parsing import episode_keys, parse_candidate
from mae.policies import evaluate
from mae.providers import make_provider
from mae.verification import check_path, probe, publish_hardlink, verify

log = logging.getLogger("mae")


class Worker:
    def __init__(self, settings, db=None, providers=None, downloader=None):
        self.settings = settings
        self.db = db or Database(settings.state_dir)
        self.providers = (
            providers if providers is not None else [make_provider(c) for c in settings.providers]
        )
        self.downloader = downloader or Downloader(settings)

    def tick(self):
        # An OS advisory lock is released even after SIGKILL. One host/worker only.
        with (self.settings.state_dir / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            self.release_idle_backups()
            self.discard_downloads()
            self.pause_stopped_downloads()
            if self.settings.jellyfin_key.get_secret_value():
                self.reconcile_completed()
            if not self.settings.dry_run:
                self.cleanup_deleted_requests()
                self.delete_orphaned_downloads()
            jobs = self.db.execute(
                "SELECT * FROM jobs WHERE next_run<=:now AND state NOT IN "
                "('AVAILABLE','CANCELLED','FAILED','NEEDS_REVIEW','AUTH_REQUIRED') ORDER BY next_run,id",
                now=time.time(),
            )
            for job in jobs:
                try:
                    self.process(job)
                except ProviderError as exc:
                    if exc.outcome in {Outcome.AUTH_REQUIRED, Outcome.ACCESS_DENIED}:
                        self.db.transition(job["id"], "AUTH_REQUIRED", exc.outcome.value)
                    else:
                        self.retry(job, exc.outcome.value, exc.retry_after)
                except (ValueError, FileNotFoundError) as exc:
                    # Exceptions are only persisted as bounded codes, never URLs/bodies.
                    code = str(exc) if re.fullmatch(r"[A-Z_]{1,80}", str(exc)) else type(exc).__name__
                    self.db.transition(job["id"], "NEEDS_REVIEW", code)
                except Exception as exc:
                    self.retry(job, type(exc).__name__)
                log.info(
                    json.dumps(
                        {"event": "job_tick", "job": job["id"], "state": self.db.job(job["id"])["state"]}
                    )
                )
            return True

    def pause_stopped_downloads(self):
        for job in self.db.downloads_to_pause():
            try:
                # Retain the torrent and files for review without continuing
                # a cancelled or changed episode acquisition.
                self.downloader.pause(job["torrent_hash"])
                self.db.mark_pause_done(job["id"])
            except Exception as exc:
                log.warning(
                    json.dumps({"event": "download_pause_failed", "job": job["id"], "type": type(exc).__name__})
                )

    def delete_orphaned_downloads(self):
        """Delete qBittorrent's copy for a job that is admin-cancelled or
        whose Jellyfin item disappeared. Safe even for a published import:
        the library copy is an independent hardlink, unaffected by deleting
        the torrent's own files.
        """
        for job in self.db.downloads_to_delete():
            try:
                self.downloader.delete_owned(job["torrent_hash"], self.settings.store / "downloads")
                self.db.finish_download_delete(job["id"], job["state"])
            except Exception as exc:
                log.warning(
                    json.dumps({"event": "download_delete_failed", "job": job["id"], "type": type(exc).__name__})
                )

    def cleanup_deleted_requests(self):
        for job in self.db.deleted_request_cleanups():
            try:
                self.cleanup_deleted_request(job)
            except ValueError as exc:
                code = str(exc) if re.fullmatch(r"[A-Z_]{1,80}", str(exc)) else type(exc).__name__
                self.db.block_deleted_request_cleanup(job["id"], code)
            except Exception as exc:
                self.db.block_deleted_request_cleanup(job["id"], "SEERR_CLEANUP_RETRY", delay=300)
                log.warning(json.dumps({"event": "seerr_cleanup_failed", "job": job["id"],
                                        "type": type(exc).__name__}))

    def cleanup_deleted_request(self, job):
        """Remove only files this job published and the torrent this job added.

        Every import is re-checked against its journaled device/inode before
        unlinking; anything unexpected stays on disk and blocks the job.
        """
        store = self.settings.store
        library_roots = (store / "films", store / "serials")
        imports = self.db.execute(
            "SELECT * FROM imports WHERE job_id=:id AND state='PUBLISHED'", id=job["id"]
        )
        planned = []
        for row in imports:
            path = Path(row["destination"])
            check_path(path, store)
            if not any(path.is_relative_to(root) and path != root for root in library_roots):
                raise ValueError("IMPORT_OUTSIDE_LIBRARY")
            try:
                current = path.lstat()
            except FileNotFoundError:
                planned.append((row, path, None))
                continue
            if not stat.S_ISREG(current.st_mode):
                raise ValueError("IMPORT_NOT_REGULAR_FILE")
            if row["device"] is None or (current.st_dev, current.st_ino) != (row["device"], row["inode"]):
                raise ValueError("IMPORT_INODE_MISMATCH")
            planned.append((row, path, current))
        hashes = {row["torrent_hash"] for row in self.db.execute(
            "SELECT torrent_hash FROM job_downloads WHERE job_id=:id", id=job["id"]
        )}
        if job["torrent_hash"]:
            hashes.add(job["torrent_hash"])
        # Older partial imports predate the ownership journal. Recover their
        # hashes before removing import records, and persist them for retries.
        if imports:
            hashes.update(self.downloader.hashes_for_sources(
                [row["source"] for row in imports], store / "downloads"
            ))
            for torrent_hash in hashes:
                self.db.execute(
                    "INSERT OR IGNORE INTO job_downloads(job_id,torrent_hash) VALUES(:id,:hash)",
                    id=job["id"], hash=torrent_hash,
                )
        for torrent_hash in hashes:
            if self.shared_download(job["id"], torrent_hash):
                raise ValueError("TORRENT_SHARED_WITH_ANOTHER_JOB")
        # All checks pass before the first destructive action.
        removed = False
        for row, path, current in planned:
            if current is not None:
                path.unlink()
                removed = True
                for parent in path.parents:
                    if parent in library_roots or not parent.is_relative_to(store):
                        break
                    try:
                        parent.rmdir()  # Only succeeds for directories left empty.
                    except OSError:
                        break
            self.db.execute("DELETE FROM imports WHERE id=:id AND state='PUBLISHED'", id=row["id"])
            self.db.execute(
                "INSERT INTO events(job_id,state,reason,created) VALUES(:id,'CANCELLED',:reason,:now)",
                id=job["id"], reason="SEERR_FILE_REMOVED", now=time.time(),
            )
        for torrent_hash in hashes:
            self.downloader.delete_owned(torrent_hash, store / "downloads")
            self.db.execute(
                "DELETE FROM job_downloads WHERE job_id=:id AND torrent_hash=:hash",
                id=job["id"], hash=torrent_hash,
            )
        self.db.finish_deleted_request_cleanup(job["id"])
        if removed and self.settings.jellyfin_key.get_secret_value():
            try:
                Jellyfin(self.settings).refresh()
            except Exception:
                pass  # Jellyfin's own scan removes the item later.

    def reconcile_completed(self):
        for job in self.db.execute(
            "SELECT * FROM jobs WHERE state='AVAILABLE' AND next_run<=:now", now=time.time()
        ):
            try:
                coverage = Jellyfin(self.settings).coverage(Work.model_validate_json(job["work"]))
                self.db.observe_completed(job, coverage)
            except Exception as exc:
                # Failed observations are unknown, never evidence that a file vanished.
                self.db.execute(
                    "UPDATE jobs SET next_run=:next WHERE id=:id AND state='AVAILABLE' AND work=:work",
                    next=time.time() + 300,
                    id=job["id"],
                    work=job["work"],
                )
                log.warning(
                    json.dumps(
                        {
                            "event": "library_reconciliation_failed",
                            "job": job["id"],
                            "type": type(exc).__name__,
                        }
                    )
                )

    def retry(self, job, reason, minimum=60):
        attempts = self.db.job(job["id"])["attempts"] + 1
        state = "FAILED" if attempts >= self.settings.max_attempts else "RETRY_SCHEDULED"
        self.db.transition(
            job["id"], state, reason, attempts=attempts, delay=max(minimum, min(3600, 30 * 2**attempts))
        )

    def process(self, job):
        current = self.db.job(job["id"])
        if current["state"] != job["state"]:
            return
        work = Work.model_validate_json(job["work"])
        if work.kind == "series":
            work = work.model_copy(update={"episodes": self.db.desired_episodes(job["id"])})
        if job["torrent_hash"]:
            return self.monitor(job, work)
        library_coverage = set()
        if self.settings.jellyfin_key.get_secret_value() or not (job["dry_run"] or self.settings.dry_run):
            library_coverage = Jellyfin(self.settings).coverage(work)
            self.db.record_coverage(job["id"], library_coverage)
            wanted = {"movie"} if work.kind == "movie" else set(work.episodes)
            if wanted and wanted.issubset(library_coverage):
                self.db.transition(
                    job["id"],
                    "AVAILABLE",
                    "ALREADY_IN_LIBRARY",
                    reserved=0,
                    decision=json.dumps({"library_coverage": sorted(library_coverage)}),
                )
                return
        covered = set()
        for row in self.db.execute(
            "SELECT episode FROM imports WHERE job_id=:id AND state='PUBLISHED'", id=job["id"]
        ):
            covered.update((row["episode"] or "").split("+"))
        covered.discard("")
        if (
            work.kind == "series"
            and covered - library_coverage
            and self.settings.jellyfin_key.get_secret_value()
        ):
            self.db.transition(job["id"], "NEEDS_REVIEW", "IMPORTED_EPISODES_MISSING_FROM_LIBRARY")
            return
        covered.update(library_coverage)
        if work.kind == "series":
            if not work.episodes:
                self.db.transition(job["id"], "WAITING_FOR_RELEASE", "NO_EPISODES_IN_METADATA", delay=3600)
                return
            monitored = set(work.episodes)
            work = work.model_copy(update={
                "episodes": sorted(monitored - covered),
                "covered_episodes": sorted(monitored & covered),
            })
            if not work.episodes:
                self.db.transition(job["id"], "AVAILABLE", reserved=0)
                return
        if not self.providers:
            self.db.transition(job["id"], "AUTH_REQUIRED", "NO_CONFIGURED_PROVIDERS")
            return
        self.db.execute(
            "INSERT INTO attempts(job_id,created,state) VALUES(:id,:now,'SEARCHING')",
            id=job["id"],
            now=time.time(),
        )
        self.db.transition(job["id"], "SEARCHING")
        selected = None
        decisions, errors = [], []
        stalled = {(row["provider"], row["release_id"]) for row in self.db.execute(
            "SELECT provider,release_id FROM stalled_releases WHERE job_id=:id", id=job["id"]
        )}
        skipped_stalled = False
        eligible = []
        for provider in self.providers:
            stage = "SEARCHING"
            try:
                results = provider.search(work)
                stage = "ENRICHING"
                for index, release in enumerate(results[:100]):
                    # Expand beyond the initial sample only while no proven match exists.
                    if selected and index >= self.settings.enrichment_limit:
                        break
                    preliminary = evaluate(
                        parse_candidate(release, provider_details_empty()), work, self.settings.policy
                    )
                    if "IDENTITY_MISMATCH" in preliminary.reasons or "SEASON_MISMATCH" in preliminary.reasons:
                        decisions.append(preliminary.model_dump())
                        continue
                    if (release.provider, release.release_id) in stalled:
                        preliminary.reasons.append("DOWNLOAD_STALLED_NO_PEERS")
                        decisions.append(preliminary.model_dump())
                        skipped_stalled = True
                        continue
                    self.db.transition(job["id"], "ENRICHING")
                    details = provider.fetch_details(release)
                    candidate = evaluate(parse_candidate(release, details), work, self.settings.policy)
                    if candidate.unknown and not candidate.reasons and details.evidence:
                        interpretation = DeepSeek(self.settings, self.db).interpret(
                            details.evidence, request_key=f"job:{job['id']}:generation:{job['generation']}"
                        )
                        if interpretation:
                            # LLM-only mandatory claims require review in this version.
                            # Exact quotes alone do not prove semantic entailment.
                            candidate.claims.extend(interpretation.claims)
                            candidate.unknown.append("llm_interpretation_requires_review")
                    decisions.append(candidate.model_dump())
                    if candidate.reasons or candidate.unknown:
                        continue
                    rank = sum(candidate.score.values())
                    eligible.append((rank, candidate, provider))
                    if selected is None or rank > selected[0]:
                        selected = (rank, candidate, provider)
            except ProviderError as exc:
                errors.append(
                    {
                        "provider": provider.config.name,
                        "stage": stage,
                        "outcome": exc.outcome.value,
                        "retry_after": exc.retry_after,
                    }
                )
        decision = json.dumps(
            {"policy": self.settings.policy.model_dump(), "candidates": decisions, "errors": errors}
        )
        self.db.transition(job["id"], "SELECTED" if selected else "WAITING_FOR_RELEASE", decision=decision)
        if not selected:
            if skipped_stalled:
                # Every usable release had no peers; seeders may return, so the
                # next hourly search tries them again.
                self.db.execute("DELETE FROM stalled_releases WHERE job_id=:id", id=job["id"])
            # Unknown properties of already rejected releases cannot turn a
            # wrong movie/season or forbidden source into a review candidate.
            state = (
                "NEEDS_REVIEW"
                if any(c["unknown"] and not c["reasons"] for c in decisions)
                else "WAITING_FOR_RELEASE"
            )
            if errors and not decisions:
                if not any(e["outcome"] in {"AUTH_REQUIRED", "ACCESS_DENIED"} for e in errors):
                    # A search result page can be valid while a later release
                    # card fetch fails. Preserve that distinction for retries
                    # and source diagnostics.
                    reason = (
                        "PROVIDER_ENRICHMENT_FAILED"
                        if any(e["stage"] == "ENRICHING" for e in errors)
                        else "PROVIDER_SEARCH_FAILED"
                    )
                    self.retry(job, reason, max(e["retry_after"] for e in errors))
                    return
                state = "AUTH_REQUIRED"
            self.db.transition(job["id"], state, "NO_PROVEN_CANDIDATE", delay=3600)
            return
        _, candidate, provider = selected
        if work.kind == "series":
            candidate.parsed["download_episodes"] = work.episodes
        self.db.transition(job["id"], "SELECTED", candidate=candidate.model_dump_json())
        if job["dry_run"] or self.settings.dry_run:
            self.db.transition(job["id"], "NEEDS_REVIEW", "DRY_RUN_SELECTED")
            return
        active = self.db.execute(
            "SELECT COUNT(*) n,SUM(reserved)+SUM(backup_reserved) bytes FROM jobs WHERE torrent_hash IS NOT NULL "
            "AND state NOT IN ('AVAILABLE','CANCELLED')"
        )[0]
        if active["n"] >= self.settings.max_downloads:
            self.db.transition(job["id"], "RETRY_SCHEDULED", "DOWNLOAD_QUEUE_LIMIT", delay=60)
            return
        torrent_hash, size, raw = self.prepare_torrent(provider, candidate, work)
        committed = size + (active["bytes"] or 0)
        if shutil.disk_usage(self.settings.store).free < committed + self.settings.reserve_bytes:
            self.db.transition(job["id"], "RETRY_SCHEDULED", "INSUFFICIENT_SPACE", delay=600)
            return
        if self.db.job(job["id"])["state"] in {"CANCELLED", "NEEDS_REVIEW", "WAITING_FOR_RELEASE"}:
            return
        # Persist intent before network action. On ambiguous add timeout, monitor hash
        # on restart; never add again blindly or issue a second candidate.
        self.db.transition(
            job["id"], "DOWNLOADING", torrent_hash=torrent_hash, reserved=size, download_started=time.time()
        )
        self.start_torrent(torrent_hash, raw, work)
        backups = [item for item in sorted(eligible, key=lambda item: -item[0]) if item[1] is not candidate]
        if backups:
            self.start_backup(job, work, backups[0], torrent_hash, committed)

    def prepare_torrent(self, provider, candidate, work):
        handle = provider.resolve_download(candidate.release)
        url = handle.url.get_secret_value()
        if url.startswith("magnet:"):
            torrent_hash, size, files = self.downloader.magnet_preflight(url, work.kind)
            raw = None
        else:
            raw = provider.http.get(
                url, max_bytes=2_000_000,
                allowed_redirect=("bulk.nnmclub.to", "/download.php")
                if provider.config.protocol == "nnmclub" else None,
            )
            torrent_hash, size, files = torrent_metadata(raw)
        # qBittorrent metadata is still untrusted provider input. Validate every
        # path before a metadata-preflighted magnet can be resumed.
        files = [str(safe_relative(file)) for file in files]
        if work.kind == "series":
            keys = set()
            for name in files:
                if Path(name).suffix.lower() in {".mkv", ".mp4", ".m4v", ".avi", ".ts"}:
                    parsed_keys = episode_keys(guessit(name))
                    if not parsed_keys:
                        raise ValueError("TORRENT_EPISODE_MAPPING_UNVERIFIED")
                    overlap = set(parsed_keys) & set(work.episodes)
                    if overlap and not set(parsed_keys).issubset(work.episodes):
                        raise ValueError("TORRENT_FILE_COVERS_UNMONITORED_EPISODE")
                    keys.update(overlap)
            if not keys:
                raise ValueError("TORRENT_OVERLAPS_EXISTING_OR_UNREQUESTED_EPISODES")
        if size > self.settings.policy.max_bytes:
            raise ValueError("TORRENT_SIZE_EXCEEDS_POLICY")
        if any(Path(f).suffix.lower() in {".rar", ".zip", ".iso", ".exe", ".bat", ".sh"} for f in files):
            raise ValueError("UNSUPPORTED_TORRENT_CONTENT")
        return torrent_hash, size, raw

    def start_torrent(self, torrent_hash, raw, work, download_limit=None):
        episodes = work.episodes if work.kind == "series" else None
        if raw is None:
            if download_limit:
                self.downloader.set_download_limit(torrent_hash, download_limit)
            if work.kind == "series":
                self.downloader.complete_selection(torrent_hash, episodes)
            else:
                self.downloader.resume(torrent_hash)
        else:
            self.downloader.add(raw, torrent_hash, work.kind, episodes, download_limit=download_limit)

    def start_backup(self, job, work, ranked, primary_hash, committed):
        """Start the runner-up slowly so a dead primary can be replaced at once."""
        _, candidate, provider = ranked
        if work.kind == "series":
            candidate.parsed["download_episodes"] = work.episodes
        try:
            torrent_hash, size, raw = self.prepare_torrent(provider, candidate, work)
        except (ProviderError, ValueError) as exc:
            log.warning(json.dumps({"event": "backup_skipped", "job": job["id"], "reason": str(exc)[:80]}))
            return
        if torrent_hash == primary_hash or self.shared_download(job["id"], torrent_hash):
            return
        if shutil.disk_usage(self.settings.store).free < committed + size + self.settings.reserve_bytes:
            return
        self.db.transition(
            job["id"], "DOWNLOADING", backup_hash=torrent_hash, backup_candidate=candidate.model_dump_json(),
            backup_reserved=size,
        )
        try:
            self.start_torrent(torrent_hash, raw, work, self.settings.backup_download_limit)
        except Exception as exc:
            log.warning(json.dumps({"event": "backup_start_failed", "job": job["id"],
                                    "type": type(exc).__name__}))
            self.queue_discard(job["id"], torrent_hash)
            self.db.transition(job["id"], "DOWNLOADING", backup_hash=None, backup_candidate=None,
                               backup_reserved=0)
            self.discard_downloads()

    def shared_download(self, job_id, torrent_hash):
        return bool(self.db.execute(
            "SELECT 1 FROM jobs WHERE (torrent_hash=:hash OR backup_hash=:hash) AND id!=:id UNION ALL "
            "SELECT 1 FROM job_downloads WHERE torrent_hash=:hash AND job_id!=:id LIMIT 1",
            hash=torrent_hash, id=job_id,
        ))

    def queue_discard(self, job_id, torrent_hash, stalled_candidate=None):
        if stalled_candidate is not None:
            release = json.loads(stalled_candidate or "{}").get("release") or {}
            if release.get("provider") and release.get("release_id"):
                self.db.execute(
                    "INSERT OR IGNORE INTO stalled_releases(job_id,provider,release_id,torrent_hash,created) "
                    "VALUES(:id,:provider,:release,:hash,:now)",
                    id=job_id, provider=release["provider"], release=release["release_id"],
                    hash=torrent_hash, now=time.time(),
                )
        self.db.execute(
            "INSERT OR IGNORE INTO discard_downloads(job_id,torrent_hash) VALUES(:id,:hash)",
            id=job_id, hash=torrent_hash,
        )

    def release_idle_backups(self):
        """A backup only races while its job is downloading."""
        for job in self.db.execute(
            "SELECT id,backup_hash FROM jobs WHERE backup_hash IS NOT NULL AND state!='DOWNLOADING'"
        ):
            self.queue_discard(job["id"], job["backup_hash"])
            self.db.execute(
                "UPDATE jobs SET backup_hash=NULL,backup_candidate=NULL,backup_reserved=0 WHERE id=:id AND backup_hash=:hash",
                id=job["id"], hash=job["backup_hash"],
            )

    def discard_downloads(self):
        """Delete queued torrents once no job refers to them as active or backup."""
        # A crash between queueing and the job update leaves intents for hashes
        # that are still current; the race is re-decided, so drop them.
        for table in ("discard_downloads", "stalled_releases"):
            self.db.execute(
                f"DELETE FROM {table} WHERE torrent_hash IS NOT NULL AND EXISTS(SELECT 1 FROM jobs j "
                f"WHERE j.id={table}.job_id AND {table}.torrent_hash IN (j.torrent_hash,j.backup_hash))"
            )
        for row in self.db.execute(
            "SELECT d.job_id,d.torrent_hash FROM discard_downloads d JOIN jobs j ON j.id=d.job_id "
            "WHERE d.torrent_hash IS NOT j.torrent_hash AND d.torrent_hash IS NOT j.backup_hash"
        ):
            try:
                if not self.shared_download(row["job_id"], row["torrent_hash"]):
                    self.downloader.delete_owned(row["torrent_hash"], self.settings.store / "downloads")
                self.db.execute(
                    "DELETE FROM job_downloads WHERE job_id=:id AND torrent_hash=:hash",
                    id=row["job_id"], hash=row["torrent_hash"],
                )
                self.db.execute(
                    "DELETE FROM discard_downloads WHERE job_id=:id AND torrent_hash=:hash",
                    id=row["job_id"], hash=row["torrent_hash"],
                )
            except Exception as exc:
                log.warning(json.dumps({"event": "download_discard_failed", "job": row["job_id"],
                                        "type": type(exc).__name__}))

    def decide_race(self, job, info):
        """After stall_seconds keep whichever torrent receives data; True if the job changed."""
        if (
            info.get("state") not in {"metaDL", "forcedMetaDL", "stalledDL", "downloading", "forcedDL"}
            or not info.get("added_on")
            or time.time() - max(float(info["added_on"]), job["download_started"] or 0) < self.settings.stall_seconds
        ):
            return False
        if int(info.get("downloaded") or 0):
            if job["backup_hash"]:
                self.queue_discard(job["id"], job["backup_hash"])
                self.db.transition(
                    job["id"], "DOWNLOADING", backup_hash=None, backup_candidate=None, backup_reserved=0, delay=30
                )
                self.discard_downloads()
                return True
            return False
        backup = self.downloader.info(job["backup_hash"]) if job["backup_hash"] else None
        if backup and int(backup.get("downloaded") or 0):
            self.downloader.set_download_limit(job["backup_hash"], -1)
            self.queue_discard(job["id"], job["torrent_hash"], job["candidate"])
            self.db.transition(
                job["id"], "DOWNLOADING", "BACKUP_PROMOTED", delay=30,
                torrent_hash=job["backup_hash"], candidate=job["backup_candidate"],
                reserved=int(backup.get("size") or job["reserved"] or 0),
                backup_hash=None, backup_candidate=None, backup_reserved=0,
            )
        else:
            self.queue_discard(job["id"], job["torrent_hash"], job["candidate"])
            if job["backup_hash"]:
                self.queue_discard(job["id"], job["backup_hash"], job["backup_candidate"])
            self.db.transition(
                job["id"], "RETRY_SCHEDULED", "DOWNLOAD_STALLED_NO_PEERS",
                torrent_hash=None, candidate=None, reserved=0, backup_hash=None, backup_candidate=None,
                backup_reserved=0,
            )
        self.discard_downloads()
        return True

    def monitor(self, job, work):
        info = self.downloader.info(job["torrent_hash"])
        if info is None:
            self.retry(job, "ADD_OUTCOME_UNCONFIRMED")
            return
        if "mae-selection-pending" in {x.strip() for x in info.get("tags", "").split(",")}:
            candidate = json.loads(job["candidate"] or "{}")
            wanted = candidate.get("parsed", {}).get("download_episodes")
            if not isinstance(wanted, list) or not wanted:
                raise ValueError("TORRENT_SELECTION_INTENT_MISSING")
            self.downloader.complete_selection(job["torrent_hash"], wanted)
            info = self.downloader.info(job["torrent_hash"])
        if info.get("state") in {"error", "missingFiles"}:
            raise ValueError("DOWNLOAD_FILES_OR_STORAGE_ERROR")
        if float(info["progress"]) < 1:
            if self.decide_race(job, info):
                return
            self.db.transition(job["id"], "DOWNLOADING", delay=30)
            return
        if self.settings.dry_run or job["dry_run"]:
            self.db.transition(job["id"], "NEEDS_REVIEW", "DRY_RUN_PREVENTS_IMPORT")
            return
        self.db.transition(job["id"], "VERIFYING")
        paths = []
        selected_episodes = set(json.loads(job["candidate"] or "{}").get("parsed", {}).get(
            "download_episodes", work.episodes
        )) if work.kind == "series" else set()
        for file in self.downloader.files(job["torrent_hash"]):
            if file.get("priority", 1) == 0:
                continue
            relative = safe_relative(file["name"])
            if relative.suffix.lower() not in {".mkv", ".mp4", ".m4v", ".avi", ".ts"}:
                continue
            keys = episode_keys(guessit(str(relative))) if work.kind == "series" else []
            if work.kind == "series":
                if not keys:
                    raise ValueError("FILE_EPISODE_MAPPING_UNVERIFIED")
                if not (set(keys) & set(work.episodes)):
                    continue
                if not set(keys).issubset(work.episodes):
                    raise ValueError("FILE_COVERS_UNMONITORED_EPISODE")
                if not set(keys).issubset(selected_episodes):
                    raise ValueError("FILE_OUTSIDE_SELECTED_EPISODES")
            source = Path(info["save_path"]) / relative
            if not source.is_relative_to(self.settings.store / "downloads"):
                raise ValueError("DOWNLOAD_OUTSIDE_MANAGED_PATH")
            expected = json.loads(job["candidate"])["parsed"] if job["candidate"] else None
            data = verify(probe(source, self.settings.store), self.settings.policy, expected)
            paths.append((source, data, keys))
        if not paths or (work.kind == "movie" and len(paths) != 1):
            raise ValueError("AMBIGUOUS_MEDIA_FILES")
        series_folder = Jellyfin(self.settings).existing_series_folder(work) if work.kind == "series" else None
        self.db.transition(job["id"], "IMPORTING")
        # All files have been probed before the first publication.
        for source, data, keys in paths:
            episode = "+".join(keys) if keys else None
            destination = destination_for(work, source, self.settings.store, episode, series_folder)
            self.db.execute(
                "INSERT OR IGNORE INTO imports(job_id,source,destination,episode,state,probe) "
                "VALUES(:id,:source,:dest,:episode,'PREPARED',:probe)",
                id=job["id"],
                source=str(source),
                dest=str(destination),
                episode=episode,
                probe=json.dumps(data),
            )
            journal = self.db.execute("SELECT * FROM imports WHERE destination=:dest", dest=str(destination))[
                0
            ]
            if journal["job_id"] != job["id"] or journal["source"] != str(source):
                raise ValueError("IMPORT_JOURNAL_COLLISION")
            if self.db.job(job["id"])["state"] in {"CANCELLED", "NEEDS_REVIEW", "WAITING_FOR_RELEASE"}:
                return
            result = publish_hardlink(source, destination, self.settings.store)
            self.db.execute(
                "UPDATE imports SET state='PUBLISHED',device=:dev,inode=:ino WHERE id=:id",
                dev=result.st_dev,
                ino=result.st_ino,
                id=journal["id"],
            )
        published_keys = {key for _, _, keys in paths for key in keys} if work.kind == "series" else {"movie"}
        Jellyfin(self.settings).refresh()
        library_coverage = Jellyfin(self.settings).coverage(work)
        self.db.record_coverage(job["id"], library_coverage)
        if not published_keys.issubset(library_coverage):
            self.db.transition(job["id"], "IMPORTING", "WAITING_FOR_JELLYFIN", delay=60)
            return
        covered = set(library_coverage)
        if work.kind == "series" and not set(work.episodes).issubset(covered):
            self.db.transition(
                job["id"],
                "WAITING_FOR_RELEASE",
                "PARTIAL_SEASON",
                torrent_hash=None,
                candidate=None,
                reserved=0,
                delay=3600,
            )
        else:
            self.db.transition(job["id"], "AVAILABLE", reserved=0)


def provider_details_empty():
    from mae.domain import ReleaseDetails

    return ReleaseDetails()
