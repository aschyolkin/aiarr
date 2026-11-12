import calendar
import fcntl
import json
import logging
import os
import re
import shutil
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from guessit import guessit

from mae.db import Database
from mae.deepseek import DeepSeek
from mae.domain import Candidate, Outcome, ProviderError, ReleaseDetails, Work
from mae.downloads import Downloader, safe_relative, torrent_metadata
from mae.errors import failure
from mae.importing import destination_for
from mae.jellyfin import Jellyfin
from mae.parsing import apply_resolution_claims, episode_keys, normalize_title, parse_candidate
from mae.policies import evaluate, score_of
from mae.providers import TorznabProvider, make_provider
from mae.verification import check_path, probe, publish_hardlink, resolution_class, verify

log = logging.getLogger("mae")


class Worker:
    dependency_retry_seconds = 300
    monitor_interval = 30
    last_monitor = 0.0
    last_speed_check = 0.0
    last_cleanup = 0.0
    last_prune = 0.0
    # An upgrade must beat the library item by a resolution step or by this many points.
    upgrade_margin = 10

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
            # Due downloads are handled by this tick's own loop; searches only
            # interleave checks once they have run longer than monitor_interval.
            self.last_monitor = time.monotonic()
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
                self.run_job(job)
            self.apply_speed_schedule()
            if not self.settings.dry_run:
                self.cleanup_seeded()
                self.check_upgrades()
            self.prune_stats()
            return True

    def run_job(self, job):
        current = self.db.job(job["id"])
        # A download may already have been checked while another job searched.
        if current["state"] != job["state"] or current["next_run"] > time.time():
            return
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
            if code in self.PREPARE_REJECTS and self.skip_release(job, code):
                return
            self.db.transition(job["id"], "NEEDS_REVIEW", code)
        except Exception as exc:
            code, dependency = failure(exc, self.settings)
            if dependency:
                # Our own service is down: wait for it without spending the job's attempts.
                self.db.transition(job["id"], "RETRY_SCHEDULED", code, delay=self.dependency_retry_seconds)
            else:
                self.retry(job, code)
            log.warning(json.dumps({"event": "job_failed", "job": job["id"], "code": code}))
        log.info(json.dumps({"event": "job_tick", "job": job["id"], "state": self.db.job(job["id"])["state"]}))

    def monitor_downloads(self, searching_job_id):
        """Check due downloads between card fetches so a long search never delays them."""
        if time.monotonic() - self.last_monitor < self.monitor_interval:
            return
        self.last_monitor = time.monotonic()
        for job in self.db.execute(
            "SELECT * FROM jobs WHERE torrent_hash IS NOT NULL AND next_run<=:now AND id!=:id AND state IN "
            "('DOWNLOADING','RETRY_SCHEDULED')", now=time.time(), id=searching_job_id,
        ):
            self.run_job(job)

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
        due = self.db.execute("SELECT * FROM jobs WHERE state='AVAILABLE' AND next_run<=:now", now=time.time())
        if not due:
            return
        jellyfin = Jellyfin(self.settings)
        try:
            # One listing answers every movie; without it each job asks on its own.
            jellyfin.prime()
        except Exception:
            pass
        for job in due:
            try:
                work = Work.model_validate_json(job["work"])
                coverage = jellyfin.coverage(work)
                self.db.observe_completed(job, coverage, self.unaired(work, work.episodes))
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
                            "code": failure(exc, self.settings)[0],
                        }
                    )
                )

    def prune_stats(self):
        if time.monotonic() - self.last_prune < 3600 and self.last_prune:
            return
        self.last_prune = time.monotonic()
        self.db.execute("DELETE FROM provider_stats WHERE created<:old", old=time.time() - 30 * 86400)
        self.db.execute(
            "DELETE FROM card_cache WHERE created<:old", old=time.time() - self.settings.card_cache_hours * 3600
        )

    def speed_limited_now(self, now=None):
        """Whether the local hour falls in [from, to); the window may cross midnight."""
        start, end = self.settings.speed_limited_from_hour, self.settings.speed_limited_to_hour
        hour = int(((time.time() if now is None else now) / 3600 + self.settings.utc_offset_hours) % 24)
        if start == end:
            return False
        return start <= hour < end if start < end else hour >= start or hour < end

    def apply_speed_schedule(self):
        """Switch qBittorrent's alternative speed limits on the configured schedule."""
        if self.last_speed_check and time.monotonic() - self.last_speed_check < 60:
            return
        self.last_speed_check = time.monotonic()
        marker = self.settings.state_dir / "speed-schedule-active"
        try:
            if self.settings.speed_schedule_enabled:
                self.downloader.apply_speed_limits(
                    self.speed_limited_now(), self.settings.speed_limit_download, self.settings.speed_limit_upload
                )
                marker.touch()
            elif marker.exists():
                # The schedule was switched off: lift the limit aiarr had set, once.
                if self.downloader.speed_limited():
                    self.downloader.client.transfer_set_speed_limits_mode(intended_state=False)
                marker.unlink()
        except Exception as exc:
            log.warning(json.dumps({"event": "speed_schedule_failed", "type": type(exc).__name__}))

    def cleanup_seeded(self):
        """Remove torrents of finished imports from qBittorrent after cleanup_after_days
        or once they have been shared up to cleanup_ratio.

        The library file is a separate hardlink and stays; only torrents whose
        every imported file is still the journaled library inode are removed.
        """
        days, ratio = self.settings.cleanup_after_days, self.settings.cleanup_ratio
        if not (days or ratio) or (self.last_cleanup and time.monotonic() - self.last_cleanup < 600):
            return
        self.last_cleanup = time.monotonic()
        rows = self.db.execute(
            "SELECT d.job_id,d.torrent_hash,j.torrent_hash current FROM job_downloads d JOIN jobs j ON j.id=d.job_id "
            "WHERE j.replacing=0 AND d.torrent_hash IS NOT j.backup_hash AND (j.state='AVAILABLE' OR "
            "(j.state='WAITING_FOR_RELEASE' AND d.torrent_hash IS NOT j.torrent_hash))"
        )
        for row in rows:
            try:
                self.cleanup_torrent(row, days, ratio)
            except Exception as exc:
                log.warning(json.dumps({"event": "seed_cleanup_failed", "job": row["job_id"],
                                        "type": type(exc).__name__}))

    def cleanup_torrent(self, row, days, ratio=0):
        torrent_hash = row["torrent_hash"]
        info = self.downloader.info(torrent_hash)
        if info is None or float(info.get("progress") or 0) < 1:
            return
        completed = float(info.get("completion_on") or 0)
        old = bool(days) and completed > 0 and time.time() - completed >= days * 86400
        shared = bool(ratio) and float(info.get("ratio") or 0) >= ratio
        if not (old or shared):
            return
        save_path = Path(info.get("save_path") or "")
        files = {str(save_path / safe_relative(f["name"])) for f in self.downloader.files(torrent_hash)}
        imports = [
            r for r in self.db.execute(
                "SELECT * FROM imports WHERE job_id=:id AND state='PUBLISHED'", id=row["job_id"]
            ) if r["source"] in files
        ]
        if not imports or self.shared_download(row["job_id"], torrent_hash):
            return
        for item in imports:
            try:
                current = Path(item["destination"]).lstat()
            except FileNotFoundError:
                return
            if (current.st_dev, current.st_ino) != (item["device"], item["inode"]):
                return
        self.downloader.delete_owned(torrent_hash, self.settings.store / "downloads")
        self.db.execute("DELETE FROM job_downloads WHERE job_id=:id AND torrent_hash=:hash",
                        id=row["job_id"], hash=torrent_hash)
        self.db.execute("UPDATE jobs SET torrent_hash=NULL WHERE id=:id AND torrent_hash=:hash",
                        id=row["job_id"], hash=torrent_hash)
        self.db.execute(
            "INSERT INTO events(job_id,state,reason,created) SELECT id,state,'SEEDING_CLEANED',:now "
            "FROM jobs WHERE id=:id", id=row["job_id"], now=time.time(),
        )

    def check_upgrades(self):
        """Re-search one available movie or series per tick for a clearly better release."""
        hours = self.settings.upgrade_check_hours
        if not hours or not self.providers:
            return
        for job in self.db.execute(
            "SELECT * FROM jobs WHERE state='AVAILABLE' AND replacing=0 AND dry_run=0 "
            "AND upgrade_checked<:due ORDER BY upgrade_checked,id LIMIT 1", due=time.time() - hours * 3600,
        ):
            # Marked first: a failing source must not re-run the search every tick.
            self.db.execute("UPDATE jobs SET upgrade_checked=:now WHERE id=:id", id=job["id"], now=time.time())
            try:
                self.check_upgrade(job)
            except Exception as exc:
                log.warning(json.dumps({"event": "upgrade_check_failed", "job": job["id"],
                                        "type": type(exc).__name__}))

    def library_quality(self, job, work):
        """(resolution, score) of the library item, re-scored under the current policy."""
        resolution = max((self.import_resolution(row) for row in self.db.execute(
            "SELECT probe FROM imports WHERE job_id=:id AND state='PUBLISHED'", id=job["id"]
        )), default=0)
        score = 0
        if job["candidate"]:
            stored = Candidate.model_validate_json(job["candidate"])
            if not resolution:
                resolution = int(str(stored.parsed.get("screen_size") or "0p")[:-1] or 0)
            details = ReleaseDetails(evidence=[e for e in stored.evidence if e.id != "title"])
            rescored = evaluate(parse_candidate(stored.release, details, list(self.settings.policy.studio_weights)),
                                work, self.settings.policy)
            # LLM-confirmed facts are not re-derived here; keep the stored score then.
            score = sum((rescored.score if not rescored.unknown else stored.score).values())
        return resolution, score

    @staticmethod
    def import_resolution(row):
        resolution = 0
        for stream in json.loads(row["probe"] or "{}").get("streams", []):
            if stream.get("codec_type") == "video":
                resolution = max(resolution, resolution_class(stream.get("width", 0), stream.get("height", 0)) or 0)
        return resolution

    def owned_episodes(self, job_id):
        """Episode key -> resolution of the files this job itself put in the library."""
        owned = {}
        for row in self.db.execute(
            "SELECT episode,probe FROM imports WHERE job_id=:id AND state='PUBLISHED'", id=job_id
        ):
            for key in filter(None, (row["episode"] or "").split("+")):
                owned[key] = self.import_resolution(row)
        return owned

    def check_series_upgrade(self, job, work):
        """A release in a higher preferred resolution than the episodes it would replace.

        Only files aiarr imported are replaced; a series is compared by
        resolution alone, since its episodes can come from several releases.
        """
        owned = self.owned_episodes(job["id"])
        best = None
        if owned:
            search = work.model_copy(update={"episodes": sorted(owned), "covered_episodes": []})
            _, _, eligible, _ = self.search_candidates(job, search, use_llm=False, progress=False)
            preferred = set(self.settings.policy.resolutions)
            for rank, candidate, provider in eligible:
                size = int(str(candidate.parsed.get("screen_size") or "0p")[:-1] or 0)
                season = candidate.parsed.get("season")
                keys = set(episode_keys(candidate.parsed)) or {
                    key for key in owned if isinstance(season, int) and key.startswith(f"S{season:02d}E")
                }
                replaced = keys & set(owned)
                if size not in preferred or not replaced:
                    continue
                current = min(owned[key] for key in replaced)
                if size > current and (best is None or (size, len(replaced), rank) > best[0]):
                    best = ((size, len(replaced), rank), candidate, current)
        if best is None:
            self.db.execute("UPDATE jobs SET upgrade_candidate=NULL WHERE id=:id", id=job["id"])
            return
        (size, _, rank), candidate, current = best
        self.offer_upgrade(job, candidate, {"resolution": current, "score": 0}, {"resolution": size, "score": rank})

    def offer_upgrade(self, job, candidate, before, after):
        previous = (json.loads(job["upgrade_candidate"] or "{}").get("candidate") or {}).get("release") or {}
        payload = {"candidate": candidate.model_dump(), "found": time.time(), "from": before, "to": after}
        self.db.execute("UPDATE jobs SET upgrade_candidate=:value WHERE id=:id AND state='AVAILABLE'",
                        id=job["id"], value=json.dumps(payload, ensure_ascii=False))
        if previous.get("release_id") != candidate.release.release_id:
            self.db.execute(
                "INSERT INTO events(job_id,state,reason,created) VALUES(:id,'AVAILABLE','UPGRADE_AVAILABLE',:now)",
                id=job["id"], now=time.time(),
            )
        if self.settings.auto_upgrade:
            self.db.choose_release(job["id"], candidate.release.provider, candidate.release.release_id,
                                   replacing=True)

    def check_upgrade(self, job):
        work = Work.model_validate_json(job["work"])
        if work.kind == "series":
            return self.check_series_upgrade(job, work)
        resolution, score = self.library_quality(job, work)
        _, _, eligible, _ = self.search_candidates(job, work, use_llm=False, progress=False)
        current = json.loads(job["candidate"] or "{}").get("release") or {}
        preferred = set(self.settings.policy.resolutions)
        best = None
        for rank, candidate, provider in eligible:
            release = candidate.release
            if (release.provider, release.release_id) == (current.get("provider"), current.get("release_id")):
                continue
            size = int(str(candidate.parsed.get("screen_size") or "0p")[:-1] or 0)
            if size not in preferred:
                continue
            better = size > resolution or (size == resolution and rank >= score + self.upgrade_margin)
            if better and (best is None or (size, rank) > best[0]):
                best = ((size, rank), candidate, provider)
        if best is None:
            self.db.execute("UPDATE jobs SET upgrade_candidate=NULL WHERE id=:id", id=job["id"])
            return
        (size, rank), candidate, provider = best
        self.offer_upgrade(job, candidate, {"resolution": resolution, "score": score},
                           {"resolution": size, "score": rank})

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
        upcoming = {}
        if work.kind == "series":
            desired = self.db.desired_episodes(job["id"])
            # Announced but not yet broadcast episodes are awaited, not searched.
            upcoming = self.unaired(work, desired)
            work = work.model_copy(update={"episodes": [e for e in desired if e not in upcoming]})
        if job["torrent_hash"]:
            return self.monitor(job, work, upcoming)
        manual = json.loads(job["manual_release"] or "{}")
        manual = manual if manual.get("release_id") else None
        library_coverage = set()
        if self.settings.jellyfin_key.get_secret_value() or not (job["dry_run"] or self.settings.dry_run):
            library_coverage = Jellyfin(self.settings).coverage(work)
            self.db.record_coverage(job["id"], library_coverage)
            wanted = {"movie"} if work.kind == "movie" else set(work.episodes)
            if wanted and wanted.issubset(library_coverage) and not job["replacing"]:
                if upcoming:
                    self.wait_for_air(job, upcoming)
                    return
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
        owned = set(covered)
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
                if upcoming:
                    self.wait_for_air(job, upcoming)
                    return
                self.db.transition(job["id"], "WAITING_FOR_RELEASE", "NO_EPISODES_IN_METADATA", delay=3600)
                return
            monitored = set(work.episodes)
            if job["replacing"]:
                # A better release replaces only the files this job imported.
                work = work.model_copy(update={"episodes": sorted(monitored & owned), "covered_episodes": []})
                if not work.episodes:
                    self.db.abort_replacement(job["id"])
                    return
            else:
                work = work.model_copy(update={
                    "episodes": sorted(monitored - covered),
                    "covered_episodes": sorted(monitored & covered),
                })
            if not work.episodes:
                if upcoming:
                    self.wait_for_air(job, upcoming)
                    return
                self.db.transition(job["id"], "AVAILABLE", reserved=0)
                return
        if not self.providers:
            self.db.transition(job["id"], "AUTH_REQUIRED", "NO_CONFIGURED_PROVIDERS")
            return
        if not (job["dry_run"] or self.settings.dry_run) and self.queue_full():
            # Searching now would only repeat every source request each minute
            # until a download slot frees up; the search waits instead.
            self.db.transition(job["id"], "RETRY_SCHEDULED", "DOWNLOAD_QUEUE_LIMIT", delay=60)
            return
        self.db.execute(
            "INSERT INTO attempts(job_id,created,state) VALUES(:id,:now,'SEARCHING')",
            id=job["id"],
            now=time.time(),
        )
        self.db.transition(job["id"], "SEARCHING")
        if manual:
            chosen = self.manual_candidate(job, work, manual)
            if chosen is None:
                return
            ranked = [chosen]
        else:
            unproven = []
            decisions, errors, eligible, skipped_stalled = self.search_candidates(
                job, work, tentative=unproven if self.settings.trust_unproven else None
            )
            verdict = None
            if not eligible and unproven:
                eligible, verdict = self.judge(job, work, unproven)
            decision = json.dumps(
                {"policy": self.settings.policy.model_dump(), "candidates": decisions, "errors": errors,
                 "verdict": verdict}
            )
            self.db.transition(job["id"], "SELECTED" if eligible else "WAITING_FOR_RELEASE", decision=decision)
            if not eligible:
                if skipped_stalled:
                    # Every usable release had no peers; seeders may return, so the
                    # next hourly search tries them again.
                    self.db.execute("DELETE FROM stalled_releases WHERE job_id=:id AND slow=0", id=job["id"])
                # Unknown properties of already rejected releases cannot turn a
                # wrong movie/season or forbidden source into a review candidate.
                state = (
                    "NEEDS_REVIEW"
                    if any(c["unknown"] and not c["reasons"] for c in decisions) and not verdict
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
                self.db.transition(job["id"], state, "AI_DECLINED" if verdict else "NO_PROVEN_CANDIDATE", delay=3600)
                return
            ranked = self.rank(eligible, work, job["id"])
        _, candidate, provider = ranked[0]
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
            job["id"], "DOWNLOADING", torrent_hash=torrent_hash, reserved=size, download_started=time.time(),
            # A replacement keeps only what is needed to cancel it.
            manual_release=json.dumps({"previous_candidate": json.loads(job["manual_release"] or "{}").get(
                "previous_candidate")}) if job["replacing"] else None,
        )
        self.start_torrent(torrent_hash, raw, work)
        if len(ranked) > 1:
            self.start_backup(job, work, ranked[1], torrent_hash, committed)

    def queue_full(self):
        return self.db.execute(
            "SELECT COUNT(*) n FROM jobs WHERE torrent_hash IS NOT NULL AND state NOT IN ('AVAILABLE','CANCELLED')"
        )[0]["n"] >= self.settings.max_downloads

    def rank(self, eligible, work, job_id):
        wanted = set(work.episodes)
        slow_keys = {(row["provider"], row["release_id"]) for row in self.db.execute(
            "SELECT provider,release_id FROM stalled_releases WHERE job_id=:id AND slow=1", id=job_id
        )}

        def coverage(item):
            # For a series, more of the missing episodes beats a higher resolution.
            return len(set(episode_keys(item[1].parsed)) & wanted) if work.kind == "series" else 0

        return sorted(
            eligible, key=lambda item: (coverage(item), *self.download_preference(item, slow_keys)), reverse=True
        )

    def search_candidates(self, job, work, use_llm=True, progress=True, tentative=None):
        """Search every source; returns decisions, errors, eligible and whether stalled were skipped.

        Sources are queried in parallel, each at its own request interval.
        progress=False (upgrade checks) leaves the job's state and decision untouched.
        """
        stalled, stalled_hashes = set(), set()
        for row in self.db.execute(
            "SELECT provider,release_id,torrent_hash FROM stalled_releases WHERE job_id=:id AND slow=0", id=job["id"]
        ):
            stalled.add((row["provider"], row["release_id"]))
            stalled_hashes.add(row["torrent_hash"])
        rejected, rejected_hashes = set(), set()
        for row in self.db.execute(
            "SELECT provider,release_id,torrent_hash FROM rejected_releases WHERE job_id=:id", id=job["id"]
        ):
            rejected.add((row["provider"], row["release_id"]))
            rejected_hashes.add(row["torrent_hash"])
        studios = list(self.settings.policy.studio_weights)
        # One slot per source keeps the merged order independent of thread timing.
        slots = [{"decisions": [], "errors": [], "eligible": [], "tentative": []} for _ in self.providers]
        shared = {"selected": False, "skipped_stalled": False, "published": 0.0}
        lock = threading.Lock()
        main = threading.current_thread()

        def merged(key):
            return [item for slot in slots for item in slot[key]]

        def publish_progress(force=False):
            if not progress:
                return
            with lock:
                if not force and time.monotonic() - shared["published"] < 3:
                    return
                shared["published"] = time.monotonic()
                self.db.save_partial_decision(job["id"], json.dumps({
                    "policy": self.settings.policy.model_dump(), "candidates": merged("decisions"),
                    "errors": merged("errors"), "partial": True,
                }))

        def run(provider, slot):
            stage = "SEARCHING"
            try:
                results = self.search_all(provider, work)
                stage = "ENRICHING"
                viable = []
                for release in results:
                    preliminary = evaluate(
                        parse_candidate(release, provider_details_empty(), studios), work, self.settings.policy
                    )
                    if self.card_cannot_help(preliminary):
                        # Size, year, season, source or a title that is not this work:
                        # no release card can change that, so skip its request.
                        slot["decisions"].append(preliminary.model_dump())
                        continue
                    key = (release.provider, release.release_id)
                    # The same torrent often reaches several sources; its infohash identifies it.
                    if key in rejected or (release.infohash and release.infohash in rejected_hashes):
                        preliminary.reasons.append("VERIFICATION_FAILED")
                        slot["decisions"].append(preliminary.model_dump())
                        continue
                    if key in stalled or (release.infohash and release.infohash in stalled_hashes):
                        preliminary.reasons.append("DOWNLOAD_STALLED_NO_PEERS")
                        slot["decisions"].append(preliminary.model_dump())
                        shared["skipped_stalled"] = True
                        continue
                    viable.append((self.enrichment_priority(preliminary), release))
                # Most promising cards first, so enrichment_limit is spent on them.
                viable.sort(key=lambda item: item[0], reverse=True)
                for index, (_, release) in enumerate(viable):
                    # Expand beyond the initial sample only while no proven match exists.
                    if shared["selected"] and index >= self.settings.enrichment_limit:
                        break
                    if threading.current_thread() is main:
                        self.monitor_downloads(job["id"])
                    if progress:
                        self.db.transition(job["id"], "ENRICHING")
                    details = self.card(provider, release)
                    parsed_candidate = parse_candidate(release, details, studios)
                    candidate = evaluate(parsed_candidate, work, self.settings.policy)
                    if use_llm and candidate.unknown and not candidate.reasons:
                        # The release title is evidence too; some sources have no card.
                        interpretation = DeepSeek(self.settings, self.db).interpret(
                            parsed_candidate.evidence, request_key=f"job:{job['id']}:generation:{job['generation']}"
                        )
                        if interpretation:
                            # Claims carry exact evidence quotes; ffprobe re-checks
                            # codec, resolution and Russian audio in the downloaded file.
                            parsed_candidate.claims.extend(interpretation.claims)
                            codecs = {c.value for c in interpretation.claims if c.field == "video_codec"}
                            if len(codecs) == 1 and not parsed_candidate.parsed.get("video_codec"):
                                parsed_candidate.parsed["video_codec"] = next(iter(codecs))
                            apply_resolution_claims(parsed_candidate)
                            candidate = evaluate(parsed_candidate, work, self.settings.policy)
                    slot["decisions"].append(candidate.model_dump())
                    publish_progress()
                    if candidate.reasons:
                        continue
                    if candidate.unknown:
                        # Unconfirmed facts that ffprobe settles after the download.
                        if progress and set(candidate.unknown) <= self.TRUSTED_UNKNOWN:
                            scored = candidate.model_copy(update={"score": score_of(candidate, self.settings.policy)})
                            slot["tentative"].append((sum(scored.score.values()), scored, provider))
                        continue
                    slot["eligible"].append((sum(candidate.score.values()), candidate, provider))
                    shared["selected"] = True
            except ProviderError as exc:
                slot["errors"].append(
                    {
                        "provider": provider.config.name,
                        "stage": stage,
                        "outcome": exc.outcome.value,
                        "retry_after": exc.retry_after,
                    }
                )
            publish_progress(force=True)

        if len(self.providers) < 2:
            for provider, slot in zip(self.providers, slots):
                run(provider, slot)
        else:
            with ThreadPoolExecutor(max_workers=min(len(self.providers), 8)) as pool:
                pending = {pool.submit(run, provider, slot) for provider, slot in zip(self.providers, slots)}
                finished = set()
                while pending:
                    done, pending = wait(pending, timeout=1)
                    finished |= done
                    # Downloads keep being checked while the sources answer.
                    self.monitor_downloads(job["id"])
                for future in finished:
                    future.result()
        if tentative is not None:
            tentative.extend(self.unique_torrents(merged("tentative")))
        return merged("decisions"), merged("errors"), self.unique_torrents(merged("eligible")), shared[
            "skipped_stalled"]

    def judge(self, job, work, unproven):
        """Decide on releases with unconfirmed facts: DeepSeek picks one or declines, and says why.

        Without DeepSeek (no key, budget used up, error) the best-scored release is taken by rule.
        Returns (eligible, verdict); an empty eligible list means the release was declined.
        """
        ranked = self.rank(unproven, work, job["id"])[:5]
        answer = DeepSeek(self.settings, self.db).decide(
            work, [item[1] for item in ranked],
            request_key=f"verdict:{job['id']}:generation:{job['generation']}",
        )
        if answer is None:
            title = ranked[0][1].release.title
            return ranked[:1], {
                "download": True, "by": "rules", "release": title,
                "reason_ru": "ИИ недоступен: взят лучший по баллам релиз, часть данных не подтверждена. "
                             "Файл проверит ffprobe, при расхождении будет взят следующий.",
                "reason_en": "The AI is unavailable: the best-scored release was taken although some facts are "
                             "unconfirmed. ffprobe will check the file and the next release is used if it fails.",
            }
        verdict = {"download": answer.download, "by": "deepseek", "reason_ru": answer.reason_ru,
                   "reason_en": answer.reason_en}
        if not answer.download:
            return [], verdict
        verdict["release"] = ranked[answer.choice][1].release.title
        return [ranked[answer.choice]], verdict

    # Unknown facts the downloaded file settles: size (checked against the torrent metadata),
    # the Russian audio track and the codec (both read by ffprobe).
    TRUSTED_UNKNOWN = frozenset({"size", "russian_audio", "compatible_video_codec"})

    @staticmethod
    def unique_torrents(eligible):
        """One entry per torrent: the same infohash from several sources is one download."""
        best, order = {}, []
        for item in eligible:
            release = item[1].release
            key = release.infohash or (release.provider, release.release_id)
            if key not in best:
                order.append(key)
                best[key] = item
            elif (item[0], release.seeders or 0) > (best[key][0], best[key][1].release.seeders or 0):
                best[key] = item
        return [best[key] for key in order]

    def card(self, provider, release):
        """A release card, from the cache while it is younger than card_cache_hours."""
        hours = self.settings.card_cache_hours
        name = str(provider.config.name)
        if hours:
            rows = self.db.execute(
                "SELECT details FROM card_cache WHERE provider=:provider AND release_id=:release AND created>=:since",
                provider=name, release=release.release_id, since=time.time() - hours * 3600,
            )
            if rows:
                try:
                    return ReleaseDetails.model_validate_json(rows[0]["details"])
                except ValueError:
                    pass
        details = self.timed(provider, "ENRICHING", lambda: provider.fetch_details(release))
        if hours and isinstance(details, ReleaseDetails):
            self.db.execute(
                "INSERT OR REPLACE INTO card_cache(provider,release_id,details,created) "
                "VALUES(:provider,:release,:details,:now)",
                provider=name, release=release.release_id, details=details.model_dump_json(), now=time.time(),
            )
        return details

    # Rejections a download cannot overcome: the torrent check or ffprobe
    # verification would refuse the file anyway.
    UNCHOOSABLE = frozenset({"TOO_LARGE", "FILE_TOO_LARGE", "VIDEO_CODEC_NOT_ALLOWED", "NOT_VIDEO_RELEASE"})

    @classmethod
    def choosable(cls, candidate):
        reasons = set(candidate.get("reasons") or [])
        if reasons & cls.UNCHOOSABLE:
            return False
        parsed = candidate.get("parsed") or {}
        return not ("RESOLUTION_NOT_ALLOWED" in reasons and parsed.get("screen_size"))

    def manual_candidate(self, job, work, manual):
        """Find the administrator's release again; its download handle lives only in a search."""
        provider = next((p for p in self.providers if p.config.name == manual.get("provider")), None)
        release = None
        if provider is not None:
            if manual.get("query"):
                # Found by the administrator's own search text.
                text_query = work.model_copy(update={"title": str(manual["query"])[:250], "year": None})
                results = self.timed(provider, "SEARCHING", lambda: provider.search(text_query))
            else:
                results = self.search_all(provider, work)
            release = next((r for r in results if r.release_id == manual.get("release_id")), None)
        if release is None:
            reason = "MANUAL_RELEASE_SOURCE_GONE" if provider is None else "MANUAL_RELEASE_NOT_FOUND"
            if job["replacing"]:
                self.db.abort_replacement(job["id"])
                self.db.execute(
                    "INSERT INTO events(job_id,state,reason,created) VALUES(:id,'AVAILABLE',:reason,:now)",
                    id=job["id"], reason=reason, now=time.time(),
                )
            else:
                self.db.transition(job["id"], "NEEDS_REVIEW", reason, manual_release=None)
            return None
        details = self.card(provider, release)
        candidate = evaluate(
            parse_candidate(release, details, list(self.settings.policy.studio_weights)), work, self.settings.policy
        )
        if not self.choosable(candidate.model_dump()):
            self.db.transition(job["id"], "NEEDS_REVIEW", "MANUAL_RELEASE_NOT_DOWNLOADABLE", manual_release=None)
            return None
        # The administrator overrides title/year/audio doubts; the torrent
        # check and ffprobe verification still apply.
        candidate.parsed["manual"] = True
        return sum(candidate.score.values()), candidate, provider

    def unaired(self, work, episodes):
        """Episodes with a future or unannounced TMDB air date, by local date."""
        today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + self.settings.utc_offset_hours * 3600))
        return {
            key: work.air_dates[key] for key in episodes
            if key in work.air_dates and (not work.air_dates[key] or work.air_dates[key] > today)
        }

    def wait_for_air(self, job, upcoming, **updates):
        """Sleep until the next episode's air date, re-checking at least weekly."""
        dates = sorted(date for date in upcoming.values() if date)
        delay = 86400
        if dates:
            year, month, day = (int(part) for part in dates[0].split("-"))
            start = calendar.timegm((year, month, day, 0, 0, 0)) - self.settings.utc_offset_hours * 3600
            delay = start - time.time()
        self.db.transition(
            job["id"], "WAITING_FOR_RELEASE", "NEXT_EPISODE_NOT_AIRED",
            delay=max(3600, min(delay, 7 * 86400)), **updates,
        )

    def timed(self, provider, stage, call):
        """Run one source request and journal its latency and outcome."""
        started = time.monotonic()
        outcome, results = "OK", None
        try:
            value = call()
            if isinstance(value, list):
                results = len(value)
            return value
        except ProviderError as exc:
            outcome = exc.outcome.value
            raise
        except Exception:
            outcome = "ERROR"
            raise
        finally:
            try:
                self.db.execute(
                    "INSERT INTO provider_stats(provider,stage,outcome,latency_ms,results,created) "
                    "VALUES(:provider,:stage,:outcome,:ms,:results,:now)",
                    provider=str(provider.config.name), stage=stage, outcome=outcome,
                    ms=int((time.monotonic() - started) * 1000), results=results, now=time.time(),
                )
            except Exception:
                pass  # Diagnostics never break a search.

    # Reasons decided by the release title alone; a card cannot overturn them.
    TITLE_FINAL = frozenset({
        "IDENTITY_MISMATCH", "SEASON_MISMATCH", "EPISODE_MISMATCH", "YEAR_UNVERIFIED", "NOT_A_MOVIE",
        "NOT_VIDEO_RELEASE", "SOURCE_NOT_ALLOWED", "TOO_LARGE", "FILE_TOO_LARGE", "VIDEO_CODEC_NOT_ALLOWED",
    })

    def card_cannot_help(self, preliminary):
        if set(preliminary.reasons) & self.TITLE_FINAL:
            return True
        # A card can only supply a resolution the title does not state.
        return "RESOLUTION_NOT_ALLOWED" in preliminary.reasons and bool(preliminary.parsed.get("screen_size"))

    def enrichment_priority(self, preliminary):
        """Preferred resolution, then unlabeled, then fallback; Russian marker; seeders."""
        size = str(preliminary.parsed.get("screen_size") or "")
        tier = 2 if size in {f"{n}p" for n in self.settings.policy.resolutions} else 1 if not size else 0
        russian = any(c.field == "audio_language" for c in preliminary.claims)
        return tier, russian, preliminary.release.seeders or 0

    def search_all(self, provider, work):
        """Search by the title, then once by a Latin-script alias (e.g. the
        original title); both find 'Рус / Eng' releases a single query misses."""
        results = list(self.timed(provider, "SEARCHING", lambda: provider.search(work))[:100])
        seen = {(r.provider, r.release_id) for r in results}
        wanted = normalize_title(work.title)
        for alias in work.aliases:
            if (
                normalize_title(alias) in ("", wanted)
                or not re.search(r"[A-Za-z]", alias)
                or re.search(r"[А-Яа-яЁё]", alias)
            ):
                continue
            try:
                alias_work = work.model_copy(update={"title": alias})
                extra = self.timed(provider, "SEARCHING", lambda: provider.search(alias_work))[:100]
            except ProviderError as exc:
                log.warning(json.dumps({"event": "alias_search_failed", "provider": provider.config.name,
                                        "outcome": exc.outcome.value}))
                break
            fresh = [r for r in extra if (r.provider, r.release_id) not in seen]
            seen.update((r.provider, r.release_id) for r in fresh)
            results.extend(fresh)
            break
        if isinstance(provider, TorznabProvider):
            # Indexers that link releases to TMDB/TVDB ids find renamed titles too.
            try:
                linked = provider.search_by_id(work)[:100]
            except ProviderError as exc:
                log.warning(json.dumps({"event": "id_search_failed", "provider": provider.config.name,
                                        "outcome": exc.outcome.value}))
                linked = []
            results.extend(r for r in linked if (r.provider, r.release_id) not in seen)
        return results

    def download_preference(self, item, slow_keys=frozenset()):
        """Preferred resolution, then enough seeders (unknown in between), score, seeders.

        Releases already found too slow are only a last resort.
        """
        rank, candidate, _ = item
        preferred = self.preferred_resolution(candidate.parsed)
        seeders = candidate.release.seeders
        tier = 1 if seeders is None else 2 if seeders >= self.settings.min_seeders else 0
        if (candidate.release.provider, candidate.release.release_id) in slow_keys:
            tier = -1
        return preferred, tier, rank, seeders or 0

    def prepare_torrent(self, provider, candidate, work):
        handle = provider.resolve_download(candidate.release)
        url = handle.url.get_secret_value()
        if url.startswith("magnet:"):
            torrent_hash, size, files = self.downloader.magnet_preflight(url, work.kind, with_sizes=True)
            raw = None
        else:
            raw = provider.http.get(
                url, max_bytes=2_000_000,
                allowed_redirect=("bulk.nnmclub.to", "/download.php")
                if provider.config.protocol == "nnmclub" else None,
            )
            torrent_hash, size, files = torrent_metadata(raw, with_sizes=True)
        # qBittorrent metadata is still untrusted provider input. Validate every
        # path before a metadata-preflighted magnet can be resumed.
        videos = {".mkv", ".mp4", ".m4v", ".avi", ".ts"}
        if any(
            Path(name).suffix.lower() in videos and (length or 0) > self.settings.policy.max_file_bytes
            for name, length in files
        ):
            raise ValueError("FILE_SIZE_EXCEEDS_POLICY")
        files = [str(safe_relative(name)) for name, _ in files]
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

    def queue_discard(self, job_id, torrent_hash, stalled_candidate=None, slow=False):
        if stalled_candidate is not None:
            release = json.loads(stalled_candidate or "{}").get("release") or {}
            if release.get("provider") and release.get("release_id"):
                self.db.execute(
                    "INSERT OR REPLACE INTO stalled_releases(job_id,provider,release_id,torrent_hash,created,slow) "
                    "VALUES(:id,:provider,:release,:hash,:now,:slow)",
                    id=job_id, provider=release["provider"], release=release["release_id"],
                    hash=torrent_hash, now=time.time(), slow=int(slow),
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

    @staticmethod
    def seeds(info):
        return max(int(info.get("num_seeds") or 0), int(info.get("num_complete") or 0))

    def healthy(self, info, elapsed):
        return (
            self.seeds(info) >= self.settings.min_seeders
            and int(info.get("downloaded") or 0) / elapsed >= self.settings.min_download_speed
        )

    def preferred_resolution(self, parsed):
        return str((parsed or {}).get("screen_size", "")) in {f"{n}p" for n in self.settings.policy.resolutions}

    def too_long(self, info, elapsed):
        """Both the current and the average rate exceed max_eta_seconds, to ignore short dips."""
        average = int(info.get("downloaded") or 0) / elapsed
        left = int(info.get("amount_left") or 0)
        eta_average = left / average if average else float("inf")
        return min(eta_average, int(info.get("eta") or 0)) > self.settings.max_eta_seconds

    def alternatives(self, job, allow_downgrade=False):
        """Acceptable releases from the last search that were not tried yet.

        A lower fallback resolution replaces a preferred one that downloads only
        when that download would take longer than max_eta_seconds.
        """
        current = json.loads(job["candidate"] or "{}").get("parsed")
        need_preferred = self.preferred_resolution(current) and not allow_downgrade
        skip = {(row["provider"], row["release_id"]) for row in self.db.execute(
            "SELECT provider,release_id FROM stalled_releases WHERE job_id=:id UNION "
            "SELECT provider,release_id FROM rejected_releases WHERE job_id=:id", id=job["id"]
        )}
        for raw in (job["candidate"], job["backup_candidate"]):
            release = json.loads(raw or "{}").get("release") or {}
            skip.add((release.get("provider"), release.get("release_id")))
        return any(
            not c.get("reasons") and not c.get("unknown")
            and (not need_preferred or self.preferred_resolution(c.get("parsed")))
            and ((c.get("release") or {}).get("provider"), (c.get("release") or {}).get("release_id")) not in skip
            for c in json.loads(job["decision"] or "{}").get("candidates", [])
        )

    def switch_release(self, job, reason, primary_moving=False, backup_moving=False):
        self.queue_discard(job["id"], job["torrent_hash"], job["candidate"], slow=primary_moving)
        if job["backup_hash"]:
            self.queue_discard(job["id"], job["backup_hash"], job["backup_candidate"], slow=backup_moving)
        self.db.transition(
            job["id"], "RETRY_SCHEDULED", reason,
            torrent_hash=None, candidate=None, reserved=0, backup_hash=None, backup_candidate=None,
            backup_reserved=0,
        )

    def decide_race(self, job, info):
        """Keep a torrent only while it downloads fast enough; True if the job changed."""
        if (
            info.get("state") not in {"metaDL", "forcedMetaDL", "stalledDL", "downloading", "forcedDL"}
            or not info.get("added_on")
        ):
            return False
        elapsed = time.time() - max(float(info["added_on"]), job["download_started"] or 0)
        if elapsed < self.settings.stall_seconds:
            return False
        changed = self.finish_race(job, info, elapsed) if job["backup_hash"] else self.check_slow(job, info, elapsed)
        if changed:
            self.discard_downloads()
        return changed

    def finish_race(self, job, info, elapsed):
        if self.healthy(info, elapsed):
            self.queue_discard(job["id"], job["backup_hash"])
            self.db.transition(
                job["id"], "DOWNLOADING", backup_hash=None, backup_candidate=None, backup_reserved=0, delay=30
            )
            return True
        moving = int(info.get("downloaded") or 0) > 0
        backup = self.downloader.info(job["backup_hash"])
        backup_moving = bool(backup and int(backup.get("downloaded") or 0))
        # The backup is speed-limited, so its seeders, not its speed, show its promise.
        too_long = moving and self.too_long(info, elapsed)
        downgrade = self.preferred_resolution(
            json.loads(job["candidate"] or "{}").get("parsed")
        ) and not self.preferred_resolution(json.loads(job["backup_candidate"] or "{}").get("parsed"))
        if backup_moving and (
            not moving or (self.seeds(backup) > self.seeds(info) and (not downgrade or too_long))
        ):
            self.downloader.set_download_limit(job["backup_hash"], -1)
            self.queue_discard(job["id"], job["torrent_hash"], job["candidate"], slow=moving)
            self.db.transition(
                job["id"], "DOWNLOADING", "BACKUP_PROMOTED", delay=30,
                torrent_hash=job["backup_hash"], candidate=job["backup_candidate"],
                reserved=int(backup.get("size") or job["reserved"] or 0),
                backup_hash=None, backup_candidate=None, backup_reserved=0,
                download_started=time.time(),
            )
        elif (moving or backup_moving) and not self.alternatives(job, allow_downgrade=too_long):
            # A slow download beats none when nothing else is left to try.
            self.queue_discard(job["id"], job["backup_hash"], job["backup_candidate"], slow=backup_moving)
            self.db.transition(
                job["id"], "DOWNLOADING", "DOWNLOAD_SLOW_NO_ALTERNATIVE", delay=30,
                backup_hash=None, backup_candidate=None, backup_reserved=0,
            )
        else:
            self.switch_release(
                job, "DOWNLOAD_TOO_SLOW" if moving or backup_moving else "DOWNLOAD_STALLED_NO_PEERS",
                primary_moving=moving, backup_moving=backup_moving,
            )
        return True

    def check_slow(self, job, info, elapsed):
        """Replace a download that would take longer than max_eta_seconds."""
        if elapsed < 2 * self.settings.stall_seconds or float(info.get("progress") or 0) >= 0.5:
            return False
        if json.loads(job["candidate"] or "{}").get("parsed", {}).get("manual"):
            return False  # The administrator chose this release; slow is their call.
        if not self.too_long(info, elapsed) or not self.alternatives(job, allow_downgrade=True):
            return False
        self.switch_release(job, "DOWNLOAD_TOO_SLOW", primary_moving=True)
        return True

    # Faults of the torrent contents seen before the download starts.
    PREPARE_REJECTS = frozenset({
        "TORRENT_EPISODE_MAPPING_UNVERIFIED", "TORRENT_OVERLAPS_EXISTING_OR_UNREQUESTED_EPISODES",
        "TORRENT_FILE_COVERS_UNMONITORED_EPISODE", "FILE_SIZE_EXCEEDS_POLICY",
    })

    def skip_release(self, job, code):
        """Drop an unusable release and search again; a manually chosen one stays for review."""
        current = self.db.job(job["id"])
        candidate = json.loads(current["candidate"] or "{}")
        if (candidate.get("parsed") or {}).get("manual") or not candidate.get("release"):
            return False
        self.db.reject_release(job["id"], current["candidate"], None, code)
        self.db.transition(job["id"], "RETRY_SCHEDULED", code, delay=5, candidate=None, reserved=0)
        return True

    # Faults of the downloaded file itself: another release may well be fine.
    VERIFY_REJECTS = frozenset({
        "MISSING_VIDEO_OR_DURATION", "VIDEO_CODEC_MISMATCH", "VIDEO_DIMENSIONS_UNSUPPORTED",
        "VIDEO_RESOLUTION_BELOW_POLICY", "VIDEO_RESOLUTION_DIFFERS_FROM_RELEASE",
        "VIDEO_CODEC_DIFFERS_FROM_RELEASE", "RUSSIAN_AUDIO_NOT_TAGGED", "AMBIGUOUS_MEDIA_FILES",
        "FILE_EPISODE_MAPPING_UNVERIFIED",
    })

    def try_next_release(self, job, code):
        """Drop a release whose file failed verification; True if the job moved on.

        The release is remembered and never selected for this job again. A
        manually chosen release and the last allowed attempt stay for review.
        """
        if code not in self.VERIFY_REJECTS:
            return False
        self.db.reject_release(job["id"], job["candidate"], job["torrent_hash"], code)
        if job["replacing"]:
            # The library still holds the previous file: just give the upgrade up.
            self.db.transition(job["id"], "DOWNLOADING", code)
            self.db.abort_replacement(job["id"])
            self.db.execute("UPDATE jobs SET upgrade_candidate=NULL WHERE id=:id", id=job["id"])
            self.discard_downloads()
            return True
        attempts = self.db.job(job["id"])["attempts"] + 1
        manual = (json.loads(job["candidate"] or "{}").get("parsed") or {}).get("manual")
        if manual:
            return False
        self.queue_discard(job["id"], job["torrent_hash"])
        if job["backup_hash"]:
            self.queue_discard(job["id"], job["backup_hash"])
        exhausted = attempts >= self.settings.max_attempts
        # Out of attempts: rejected releases stay excluded, so the hourly search only
        # tries releases that appear later instead of stopping for a review.
        self.db.transition(
            job["id"], "WAITING_FOR_RELEASE" if exhausted else "RETRY_SCHEDULED", code,
            attempts=0 if exhausted else attempts, torrent_hash=None, candidate=None, reserved=0,
            backup_hash=None, backup_candidate=None, backup_reserved=0, **({"delay": 3600} if exhausted else {}),
        )
        self.discard_downloads()
        return True

    def verified_files(self, job, work, info):
        """(source, probe, episode keys) of every wanted video file, each checked by ffprobe."""
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
        return paths

    def monitor(self, job, work, upcoming=None):
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
        try:
            paths = self.verified_files(job, work, info)
        except ValueError as exc:
            if self.try_next_release(job, str(exc)):
                return
            raise
        series_folder = Jellyfin(self.settings).existing_series_folder(work) if work.kind == "series" else None
        self.db.transition(job["id"], "IMPORTING")
        # A better release replaces the movie only after it has been verified.
        aside = self.set_aside_replaced(job, paths) if job["replacing"] else []
        try:
            published = self.publish(job, work, paths, series_folder)
        except BaseException:
            self.restore_replaced(aside)
            raise
        if not published:
            self.restore_replaced(aside)
            return
        if job["replacing"]:
            self.finish_replaced(job, aside)
        published_keys = {key for _, _, keys in paths for key in keys} if work.kind == "series" else {"movie"}
        Jellyfin(self.settings).refresh()
        library_coverage = Jellyfin(self.settings).coverage(work)
        self.db.record_coverage(job["id"], library_coverage)
        if not published_keys.issubset(library_coverage):
            self.db.transition(job["id"], "IMPORTING", "WAITING_FOR_JELLYFIN", delay=60)
            return
        covered = set(library_coverage)
        if job["replacing"]:
            done = dict(reserved=0, replacing=0, manual_release=None, upgrade_candidate=None,
                        upgrade_checked=time.time())
            if upcoming:
                self.wait_for_air(job, upcoming, torrent_hash=None, candidate=None, **done)
            else:
                self.db.transition(job["id"], "AVAILABLE", "UPGRADED", **done)
        elif work.kind == "series" and not set(work.episodes).issubset(covered):
            self.db.transition(
                job["id"],
                "WAITING_FOR_RELEASE",
                "PARTIAL_SEASON",
                torrent_hash=None,
                candidate=None,
                reserved=0,
                delay=3600,
            )
        elif upcoming:
            self.wait_for_air(job, upcoming, torrent_hash=None, candidate=None, reserved=0)
        else:
            self.db.transition(
                job["id"], "AVAILABLE", reserved=0, replacing=0,
                manual_release=None, upgrade_candidate=None, upgrade_checked=time.time(),
            )

    def publish(self, job, work, paths, series_folder):
        """Hardlink every probed file; False if the job changed meanwhile."""
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
                return False
            result = publish_hardlink(source, destination, self.settings.store)
            self.db.execute(
                "UPDATE imports SET state='PUBLISHED',device=:dev,inode=:ino WHERE id=:id",
                dev=result.st_dev,
                ino=result.st_ino,
                id=journal["id"],
            )
        return True

    REPLACED_SUFFIX = ".aiarr-replaced"

    def set_aside_replaced(self, job, paths):
        """Rename the library file being replaced; its journal row is kept in memory."""
        new_sources = {str(source) for source, _, _ in paths}
        new_keys = {key for _, _, keys in paths for key in keys}
        aside = []
        for row in self.db.execute(
            "SELECT * FROM imports WHERE job_id=:id AND state='PUBLISHED'", id=job["id"]
        ):
            if row["source"] in new_sources:
                continue  # A retry of this same import, not the old file.
            if row["episode"] and not set(row["episode"].split("+")) & new_keys:
                continue  # An episode the new release does not replace.
            path = Path(row["destination"])
            check_path(path, self.settings.store)
            try:
                current = path.lstat()
            except FileNotFoundError:
                current = None
            hidden = None
            if current is not None:
                if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
                    row["device"], row["inode"]
                ):
                    raise ValueError("IMPORT_INODE_MISMATCH")
                hidden = path.with_name(path.name + self.REPLACED_SUFFIX)
                os.rename(path, hidden)
            self.db.execute("DELETE FROM imports WHERE id=:id", id=row["id"])
            aside.append((row, path, hidden))
        return aside

    def restore_replaced(self, aside):
        for row, path, hidden in aside:
            if hidden is not None and hidden.exists() and not path.exists():
                os.rename(hidden, path)
            self.db.execute(
                "INSERT OR IGNORE INTO imports(id,job_id,source,destination,episode,state,probe,device,inode) "
                "VALUES(:id,:job_id,:source,:destination,:episode,:state,:probe,:device,:inode)", **row
            )

    def finish_replaced(self, job, aside):
        for _, _, hidden in aside:
            if hidden is not None and hidden.exists():
                hidden.unlink()
        # The old release's torrent only seeds a file no longer in the library.
        kept = {row["source"] for row in self.db.execute(
            "SELECT source FROM imports WHERE job_id=:id AND state='PUBLISHED'", id=job["id"]
        )}
        for row in self.db.execute(
            "SELECT torrent_hash FROM job_downloads WHERE job_id=:id AND torrent_hash!=:current",
            id=job["id"], current=job["torrent_hash"],
        ):
            files = set()
            if job["identity"].startswith("series:"):
                # A series torrent may still back episodes left in place.
                try:
                    info = self.downloader.info(row["torrent_hash"])
                    files = {
                        str(Path(info["save_path"]) / safe_relative(f["name"]))
                        for f in self.downloader.files(row["torrent_hash"])
                    } if info else set()
                except Exception:
                    continue  # Unknown content keeps seeding.
            if not files & kept:
                self.queue_discard(job["id"], row["torrent_hash"])


def provider_details_empty():
    from mae.domain import ReleaseDetails

    return ReleaseDetails()
