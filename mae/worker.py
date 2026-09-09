import fcntl
import json
import logging
import re
import shutil
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
from mae.verification import probe, publish_hardlink, verify

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
            self.pause_cancelled_downloads()
            if self.settings.jellyfin_key.get_secret_value():
                self.reconcile_completed()
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

    def pause_cancelled_downloads(self):
        for job in self.db.cancelled_downloads_to_pause():
            try:
                # Retain the torrent and files for tracker policy/review, but do
                # not let a cancelled acquisition consume further bandwidth.
                self.downloader.pause(job["torrent_hash"])
                self.db.mark_cancel_pause_done(job["id"])
            except Exception as exc:
                log.warning(
                    json.dumps({"event": "cancel_pause_failed", "job": job["id"], "type": type(exc).__name__})
                )

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
        work = Work.model_validate_json(job["work"])
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
            work = work.model_copy(update={"episodes": sorted(set(work.episodes) - covered)})
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
        for provider in self.providers:
            try:
                results = provider.search(work)
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
                    self.db.transition(job["id"], "ENRICHING")
                    details = provider.fetch_details(release)
                    candidate = evaluate(parse_candidate(release, details), work, self.settings.policy)
                    if candidate.unknown and details.evidence:
                        interpretation = DeepSeek(self.settings, self.db).interpret(details.evidence)
                        if interpretation:
                            # LLM-only mandatory claims require review in this version.
                            # Exact quotes alone do not prove semantic entailment.
                            candidate.claims.extend(interpretation.claims)
                            candidate.unknown.append("llm_interpretation_requires_review")
                    decisions.append(candidate.model_dump())
                    if candidate.reasons or candidate.unknown:
                        continue
                    rank = sum(candidate.score.values())
                    if selected is None or rank > selected[0]:
                        selected = (rank, candidate, provider)
            except ProviderError as exc:
                errors.append(
                    {
                        "provider": provider.config.name,
                        "outcome": exc.outcome.value,
                        "retry_after": exc.retry_after,
                    }
                )
        decision = json.dumps(
            {"policy": self.settings.policy.model_dump(), "candidates": decisions, "errors": errors}
        )
        self.db.transition(job["id"], "SELECTED" if selected else "WAITING_FOR_RELEASE", decision=decision)
        if not selected:
            state = "NEEDS_REVIEW" if any(c["unknown"] for c in decisions) else "WAITING_FOR_RELEASE"
            if errors and not decisions:
                if not any(e["outcome"] in {"AUTH_REQUIRED", "ACCESS_DENIED"} for e in errors):
                    self.retry(job, "PROVIDER_SEARCH_FAILED", max(e["retry_after"] for e in errors))
                    return
                state = "AUTH_REQUIRED"
            self.db.transition(job["id"], state, "NO_PROVEN_CANDIDATE", delay=3600)
            return
        _, candidate, provider = selected
        self.db.transition(job["id"], "SELECTED", candidate=candidate.model_dump_json())
        if job["dry_run"] or self.settings.dry_run:
            self.db.transition(job["id"], "NEEDS_REVIEW", "DRY_RUN_SELECTED")
            return
        active = self.db.execute(
            "SELECT COUNT(*) n,SUM(reserved) bytes FROM jobs WHERE torrent_hash IS NOT NULL "
            "AND state NOT IN ('AVAILABLE','CANCELLED')"
        )[0]
        if active["n"] >= self.settings.max_downloads:
            self.db.transition(job["id"], "RETRY_SCHEDULED", "DOWNLOAD_QUEUE_LIMIT", delay=60)
            return
        handle = provider.resolve_download(candidate.release)
        url = handle.url.get_secret_value()
        if url.startswith("magnet:"):
            torrent_hash, size, files = self.downloader.magnet_preflight(url, work.kind)
            raw = None
        else:
            raw = provider.http.get(url, max_bytes=2_000_000)
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
                    keys.update(parsed_keys)
            if not keys or not keys.issubset(work.episodes):
                raise ValueError("TORRENT_OVERLAPS_EXISTING_OR_UNREQUESTED_EPISODES")
        if size > self.settings.policy.max_bytes:
            raise ValueError("TORRENT_SIZE_EXCEEDS_POLICY")
        if any(Path(f).suffix.lower() in {".rar", ".zip", ".iso", ".exe", ".bat", ".sh"} for f in files):
            raise ValueError("UNSUPPORTED_TORRENT_CONTENT")
        if shutil.disk_usage(self.settings.store).free < size + self.settings.reserve_bytes + (
            active["bytes"] or 0
        ):
            self.db.transition(job["id"], "RETRY_SCHEDULED", "INSUFFICIENT_SPACE", delay=600)
            return
        if self.db.job(job["id"])["state"] == "CANCELLED":
            return
        # Persist intent before network action. On ambiguous add timeout, monitor hash
        # on restart; never add again blindly or issue a second candidate.
        self.db.transition(job["id"], "DOWNLOADING", torrent_hash=torrent_hash, reserved=size)
        if raw is None:
            self.downloader.resume(torrent_hash)
        else:
            self.downloader.add(raw, torrent_hash, work.kind)

    def monitor(self, job, work):
        info = self.downloader.info(job["torrent_hash"])
        if info is None:
            self.retry(job, "ADD_OUTCOME_UNCONFIRMED")
            return
        if info.get("state") in {"error", "missingFiles"}:
            raise ValueError("DOWNLOAD_FILES_OR_STORAGE_ERROR")
        if float(info["progress"]) < 1:
            self.db.transition(job["id"], "DOWNLOADING", delay=30)
            return
        if self.settings.dry_run or job["dry_run"]:
            self.db.transition(job["id"], "NEEDS_REVIEW", "DRY_RUN_PREVENTS_IMPORT")
            return
        self.db.transition(job["id"], "VERIFYING")
        paths = []
        for file in self.downloader.files(job["torrent_hash"]):
            relative = safe_relative(file["name"])
            if relative.suffix.lower() not in {".mkv", ".mp4", ".m4v", ".avi", ".ts"}:
                continue
            source = Path(info["save_path"]) / relative
            if not source.is_relative_to(self.settings.store / "downloads"):
                raise ValueError("DOWNLOAD_OUTSIDE_MANAGED_PATH")
            expected = json.loads(job["candidate"])["parsed"] if job["candidate"] else None
            data = verify(probe(source, self.settings.store), self.settings.policy, expected)
            keys = episode_keys(guessit(str(relative))) if work.kind == "series" else []
            if work.kind == "series" and (not keys or not set(keys).issubset(work.episodes)):
                raise ValueError("FILE_EPISODE_MAPPING_UNVERIFIED")
            paths.append((source, data, keys))
        if not paths or (work.kind == "movie" and len(paths) != 1):
            raise ValueError("AMBIGUOUS_MEDIA_FILES")
        self.db.transition(job["id"], "IMPORTING")
        # All files have been probed before the first publication.
        for source, data, keys in paths:
            episode = "+".join(keys) if keys else None
            destination = destination_for(work, source, self.settings.store, episode)
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
            if self.db.job(job["id"])["state"] == "CANCELLED":
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
