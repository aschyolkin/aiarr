import base64
import hashlib
import json
import re
import secrets
import shutil
import time

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from mae.admin import ADMIN_HTML
from mae.config import Settings
from mae.db import Database
from mae.domain import Work
from mae.runtime_config import RuntimeConfig, plain
from mae.seerr import Seerr
from mae.seerr_session import same_origin_write, seerr_admin_session


class ConfigUpdate(BaseModel):
    revision: str
    settings: dict


class Mirror(BaseModel):
    base_url: str = Field(pattern=r"^https?://[^\s/]+/?$", max_length=200)


class Choice(BaseModel):
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    release_id: str = Field(min_length=1, max_length=200)


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(default="", max_length=500)


PROWLARR_INDEXER = re.compile(r"(https?://[^/?#]+)/(\d{1,6})/api/?")


def prowlarr(provider):
    """Base URL, indexer id and key of a Torznab provider served by Prowlarr."""
    match = PROWLARR_INDEXER.fullmatch(provider.url)
    if provider.protocol != "torznab" or not provider.trusted_gateway or not match:
        return None
    return match[1], int(match[2]), provider.api_key.get_secret_value()


def prowlarr_client(base, key, timeout=90):
    return httpx.Client(base_url=base + "/api/v1", headers={"X-Api-Key": key}, timeout=timeout, trust_env=False)


def create_app(settings=None, db=None, seerr=None):
    base_settings = settings or Settings()
    config = RuntimeConfig(base_settings)
    settings = config.load()
    db = db or Database(settings.state_dir)
    injected_seerr = seerr is not None
    seerr = seerr or Seerr(settings)

    def authenticate(request: Request):
        if request.method == "GET" and request.url.path == "/admin":
            return  # Shell only; operational data still requires authentication.
        supplied = request.headers.get("X-Api-Key") or request.query_params.get("apikey") or ""
        if secrets.compare_digest(supplied, settings.api_key.get_secret_value()):
            request.state.actor = {"name": "API", "mode": "key"}
            return
        if request.url.path.startswith("/admin/"):
            try:
                user = seerr_admin_session(settings, request.headers.get("Cookie", ""))
            except httpx.HTTPError:
                raise HTTPException(503, "Seerr authentication unavailable") from None
            if user:
                if request.method not in {"GET", "HEAD"} and not same_origin_write(request):
                    raise HTTPException(403, "Same-origin request required")
                request.state.actor = {**user, "mode": "seerr"}
                return
        raise HTTPException(401, "Sign in to Seerr as administrator")

    app = FastAPI(
        title="Media Acquisition Engine",
        version="0.1.0",
        dependencies=[Depends(authenticate)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    @app.exception_handler(httpx.HTTPError)
    @app.exception_handler(RuntimeError)
    async def dependency_failure(request, exc):
        return JSONResponse(status_code=503, content={"detail": "Upstream unavailable or not configured"})

    @app.get("/health")
    def health():
        db.execute("SELECT 1")
        return {"status": "OK", "dry_run": settings.dry_run}

    @app.get("/openapi.json")
    def schema():
        return app.openapi()

    @app.get("/admin", response_class=HTMLResponse)
    def admin():
        script = ADMIN_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        return HTMLResponse(ADMIN_HTML, headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; connect-src 'self'; img-src https:; style-src 'unsafe-inline'; "
                f"script-src 'sha256-{digest}'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            ),
        })

    @app.get("/admin/jobs")
    def jobs():
        return db.jobs()

    @app.get("/admin/session")
    def admin_session(request: Request):
        return request.state.actor

    @app.get("/admin/overview")
    def overview():
        rows = db.execute(
            "SELECT j.id,j.work,j.state,j.reason,j.torrent_hash,j.dry_run,j.cancel_pause_done,"
            "(SELECT COUNT(*) FROM episodes e WHERE e.job_id=j.id AND e.monitored=1) wanted,"
            "(SELECT COUNT(*) FROM episodes e WHERE e.job_id=j.id AND e.monitored=1 AND e.available=1) ready,"
            "(SELECT COUNT(*) FROM imports i WHERE i.job_id=j.id AND i.state='PUBLISHED') imported,"
            "json_extract(j.candidate,'$.image_url') image_url,j.replacing,"
            "json_extract(j.upgrade_candidate,'$.to.resolution') upgrade_resolution,"
            "json_extract(j.upgrade_candidate,'$.from.resolution') current_resolution,"
            "json_extract(j.work,'$.air_dates') air_dates "
            "FROM jobs j WHERE j.state!='CANCELLED' ORDER BY j.id DESC"
        )
        transfer = None
        try:
            from mae.downloads import Downloader

            downloader = Downloader(settings)
            for row in rows:
                if row["torrent_hash"] and row["state"] != "AVAILABLE":
                    torrent = downloader.info(row["torrent_hash"])
                    row["progress"] = float(torrent["progress"]) if torrent else None
                    if torrent:
                        row["download_speed"] = int(torrent.get("dlspeed") or 0)
                        row["eta"] = int(torrent.get("eta") or 0)
            transfer = downloader.transfer()
        except Exception:
            pass
        try:
            usage = shutil.disk_usage(settings.store)
        except OSError:
            usage = None
        reserved = db.execute(
            "SELECT COALESCE(SUM(reserved)+SUM(backup_reserved),0) n FROM jobs WHERE torrent_hash IS NOT NULL "
            "AND state NOT IN ('AVAILABLE','CANCELLED')"
        )[0]["n"]
        return {
            "jobs": rows, "dry_run": settings.dry_run, "transfer": transfer,
            "disk": {"free": usage.free, "total": usage.total, "reserve": settings.reserve_bytes,
                     "committed": reserved} if usage else None,
            "speed_schedule": {
                "enabled": settings.speed_schedule_enabled, "from": settings.speed_limited_from_hour,
                "to": settings.speed_limited_to_hour,
            },
        }

    @app.get("/admin/jobs/{job_id}")
    def detail(job_id: int):
        job = db.job(job_id)
        download = None
        if job["torrent_hash"]:
            try:
                from mae.downloads import Downloader

                torrent = Downloader(settings).info(job["torrent_hash"])
                if torrent:
                    download = {
                        "progress": float(torrent.get("progress", 0)),
                        "state": torrent.get("state"),
                        "download_speed": int(torrent.get("dlspeed", 0)),
                        "eta": int(torrent.get("eta", 0)),
                        "remaining_bytes": int(torrent.get("amount_left", 0)),
                    }
            except Exception:
                # The job journal remains available when qBittorrent is unreachable.
                pass
        return {
            "job": job,
            "imports": db.execute("SELECT * FROM imports WHERE job_id=:id", id=job_id),
            "events": db.execute("SELECT * FROM events WHERE job_id=:id ORDER BY created,id", id=job_id),
            "episodes": db.execute(
                "SELECT episode,available,monitored FROM episodes WHERE job_id=:id ORDER BY episode", id=job_id
            ),
            "download": download,
        }

    @app.get("/admin/profiles")
    def profiles():
        return settings.policy

    @app.get("/admin/sources")
    def sources():
        return [
            {
                "name": p.name,
                "protocol": p.protocol,
                "url": p.url,
                "trusted_gateway": p.trusted_gateway,
                "source_host": p.source_host,
                "trusted_source": p.trusted_source,
                "details_configured": bool(p.details_selector),
                "api_key_configured": bool(p.api_key.get_secret_value()),
                "cookie_configured": bool(p.cookie.get_secret_value()),
                "encoding": p.encoding,
                "interval": p.interval,
            }
            for p in settings.providers
        ]

    def prowlarr_gateway():
        for provider in config.load().providers:
            if target := prowlarr(provider):
                return target
        raise HTTPException(404, "No Prowlarr-backed Torznab source configured")

    @app.get("/admin/indexers")
    def indexers():
        """All Prowlarr indexers, linked to the aiarr sources that use them.

        Passwords never leave Prowlarr; only whether a login is set.
        """
        base, _, key = prowlarr_gateway()
        linked = {}
        for provider in config.load().providers:
            if (target := prowlarr(provider)) and target[0] == base:
                linked[target[1]] = provider.name
        try:
            with prowlarr_client(base, key) as client:
                rows = client.get("/indexer").raise_for_status().json()
                status = {s.get("indexerId"): s for s in client.get("/indexerstatus").raise_for_status().json()}
        except httpx.HTTPError:
            raise HTTPException(503, "Prowlarr unavailable") from None
        result = []
        for data in rows:
            fields = {f.get("name"): f for f in data.get("fields", [])}
            state = status.get(data.get("id"), {})
            mirrors = list(dict.fromkeys(data.get("indexerUrls") or []))
            result.append({
                "indexer_id": data.get("id"),
                "name": data.get("name"),
                "privacy": data.get("privacy"),
                "enabled": bool(data.get("enable")),
                "needs_login": "username" in fields,
                "username": fields.get("username", {}).get("value") or "",
                "disabled_till": state.get("disabledTill"),
                "failing_since": state.get("initialFailure"),
                "source": linked.get(data.get("id")),
                "torznab_url": f"{base}/{data.get('id')}/api",
                "base_url": fields.get("baseUrl", {}).get("value") or (mirrors[0] if mirrors else None),
                "mirrors": mirrors,
            })
        return result

    @app.put("/admin/indexers/{indexer_id}/mirror")
    def set_indexer_mirror(indexer_id: int, payload: Mirror):
        """Switch a Prowlarr indexer to another of its definition's known addresses."""
        base, _, key = prowlarr_gateway()
        with prowlarr_client(base, key, timeout=400) as client:
            found = client.get(f"/indexer/{indexer_id}")
            if found.status_code == 404:
                raise HTTPException(404, "Indexer not found")
            data = found.raise_for_status().json()
            wanted = payload.base_url.rstrip("/") + "/"
            if wanted not in (data.get("indexerUrls") or []):
                raise HTTPException(400, "Prowlarr поддерживает только адреса из определения трекера")
            for field in data["fields"]:
                if field.get("name") == "baseUrl":
                    field["value"] = wanted
            try:
                response = client.put(f"/indexer/{indexer_id}", json=data)
            except httpx.TimeoutException:
                raise HTTPException(504, "Зеркало не ответило на проверку за 400 секунд") from None
        if response.status_code >= 400:
            try:
                messages = [e.get("errorMessage") for e in response.json() if isinstance(e, dict)]
            except (ValueError, TypeError):
                messages = []
            detail = "; ".join(m for m in messages if m)[:300] or f"Prowlarr HTTP {response.status_code}"
            raise HTTPException(400, detail)
        return {"saved": True, "base_url": wanted}

    @app.put("/admin/indexers/{indexer_id}/credentials")
    def set_indexer_credentials(indexer_id: int, payload: Credentials):
        base, _, key = prowlarr_gateway()
        # A login behind CloudFlare goes through FlareSolverr and can take minutes.
        with prowlarr_client(base, key, timeout=400) as client:
            found = client.get(f"/indexer/{indexer_id}")
            if found.status_code == 404:
                raise HTTPException(404, "Indexer not found")
            data = found.raise_for_status().json()
            if "username" not in {f.get("name") for f in data.get("fields", [])}:
                raise HTTPException(400, "This source does not use a login")
            for field in data["fields"]:
                if field.get("name") == "username":
                    field["value"] = payload.username
                elif field.get("name") == "password" and payload.password:
                    field["value"] = payload.password
            # Without forceSave Prowlarr logs in to the site before saving,
            # so wrong credentials are reported instead of stored.
            try:
                response = client.put(f"/indexer/{indexer_id}", json=data)
            except httpx.TimeoutException:
                raise HTTPException(504, "Сайт не ответил на проверку входа за 400 секунд") from None
        if response.status_code >= 400:
            try:
                messages = [e.get("errorMessage") for e in response.json() if isinstance(e, dict)]
            except (ValueError, TypeError):
                messages = []
            detail = "; ".join(m for m in messages if m)[:300] or f"Prowlarr HTTP {response.status_code}"
            raise HTTPException(400, detail)
        return {"saved": True}

    @app.get("/admin/config")
    def get_config():
        from mae.parsing import STUDIOS

        return {**config.snapshot(), "known_studios": list(STUDIOS)}

    @app.put("/admin/config")
    def put_config(update: ConfigUpdate):
        nonlocal settings, seerr
        try:
            result = config.save(update.revision, update.settings)
            settings = config.load()
            if not injected_seerr:
                seerr = Seerr(settings)
            return result
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(409 if code == "CONFIG_CHANGED_RELOAD" else 422, code) from None

    @app.post("/admin/indexers/{indexer_id}/attach")
    def attach_indexer(indexer_id: int):
        """Add a Prowlarr indexer as an aiarr Torznab source, reusing the gateway key."""
        nonlocal settings, seerr
        base, _, key = prowlarr_gateway()
        with prowlarr_client(base, key) as client:
            found = client.get(f"/indexer/{indexer_id}")
            if found.status_code == 404:
                raise HTTPException(404, "Indexer not found")
            name = found.raise_for_status().json().get("name") or f"indexer{indexer_id}"
        current = config.load()
        url = f"{base}/{indexer_id}/api"
        if any(p.url.rstrip("/") == url for p in current.providers):
            raise HTTPException(409, "Already attached")
        slug = (re.sub(r"[^a-z0-9_-]", "", name.lower().replace(".", "-").replace(" ", "-")) or "indexer")[:30]
        taken = {p.name for p in current.providers}
        candidate, n = slug if slug[0].isalpha() else "p" + slug, 2
        while candidate in taken:
            candidate, n = f"{slug}-{n}", n + 1
        providers = plain(current.providers) + [{
            "name": candidate, "protocol": "torznab", "url": url, "api_key": key, "trusted_gateway": True,
        }]
        try:
            result = config.save(config.snapshot()["revision"], {"providers": providers})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        settings = config.load()
        if not injected_seerr:
            seerr = Seerr(settings)
        return {"source": candidate, "revision": result["revision"]}

    @app.post("/admin/jobs/{job_id}/retry")
    def retry(job_id: int):
        job = db.job(job_id)
        if job["replacing"]:
            # Retrying a failed replacement means keeping the library item.
            abort_upgrade(job_id)
            return db.job(job_id)
        if job["state"] in {"AVAILABLE", "CANCELLED"}:
            raise HTTPException(409, "Re-request through Seerr")
        # A retry returns to automatic selection, also after a failed manual choice.
        db.transition(job_id, "REQUESTED", attempts=0, manual_release=None)
        return db.job(job_id)

    @app.post("/admin/jobs/{job_id}/choose")
    def choose(job_id: int, payload: Choice):
        """Download a release the administrator picked from the job's candidates.

        For an available movie this replaces the library file once the new
        one is verified; until then the old file and its torrent stay.
        """
        from mae.worker import Worker

        job = db.job(job_id)
        listed = list((json.loads(job["decision"] or "{}") or {}).get("candidates") or [])
        upgrade = (json.loads(job["upgrade_candidate"] or "{}") or {}).get("candidate")
        if upgrade:
            listed.append(upgrade)
        found = next((c for c in listed if (c.get("release") or {}).get("provider") == payload.provider
                      and (c.get("release") or {}).get("release_id") == payload.release_id), None)
        if found is None:
            raise HTTPException(404, "Release is not among this job's candidates")
        if not Worker.choosable(found):
            raise HTTPException(409, "Размер, кодек или разрешение этого релиза не пройдут проверку")
        replacing = job["state"] == "AVAILABLE" or bool(job["replacing"])
        if replacing and not job["identity"].startswith("movie:"):
            raise HTTPException(409, "Замена готового сериала не поддерживается")
        if payload.provider not in {p.name for p in settings.providers}:
            raise HTTPException(409, "Этот источник больше не подключён")
        try:
            db.choose_release(job_id, payload.provider, payload.release_id, replacing=replacing)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return db.job(job_id)

    @app.post("/admin/jobs/{job_id}/abort-upgrade")
    def abort_upgrade(job_id: int):
        try:
            if not db.abort_replacement(job_id):
                raise HTTPException(409, "No replacement in progress")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return db.job(job_id)

    @app.get("/admin/provider-stats")
    def provider_stats(days: int = 7):
        """Per source: request outcomes and latency, useful finds and chosen releases."""
        since = time.time() - max(1, min(days, 30)) * 86400
        rows = db.execute(
            "SELECT provider,stage,outcome,COUNT(*) n,AVG(latency_ms) avg_ms,MAX(created) last FROM provider_stats "
            "WHERE created>=:since GROUP BY provider,stage,outcome", since=since,
        )
        stats = {}
        for row in rows:
            entry = stats.setdefault(row["provider"], {
                "provider": row["provider"], "searches": 0, "cards": 0, "errors": {}, "search_ms": None,
                "card_ms": None, "last_ok": None, "last_error": None, "fit": 0, "chosen": 0,
            })
            stage = "searches" if row["stage"] == "SEARCHING" else "cards"
            entry[stage] += row["n"]
            if row["outcome"] == "OK":
                entry["search_ms" if stage == "searches" else "card_ms"] = round(row["avg_ms"])
                entry["last_ok"] = max(entry["last_ok"] or 0, row["last"])
            else:
                entry["errors"][row["outcome"]] = entry["errors"].get(row["outcome"], 0) + row["n"]
                if not entry["last_error"] or row["last"] > entry["last_error"]["at"]:
                    entry["last_error"] = {"at": row["last"], "outcome": row["outcome"], "stage": row["stage"]}
        for row in db.execute(
            "SELECT p.provider,p.outcome FROM provider_stats p JOIN (SELECT provider,MAX(id) id FROM provider_stats "
            "WHERE stage='SEARCHING' GROUP BY provider) last ON last.id=p.id"
        ):
            if row["provider"] in stats:
                stats[row["provider"]]["last_search_outcome"] = row["outcome"]
        for job in db.execute("SELECT decision,candidate FROM jobs WHERE state!='CANCELLED'"):
            for candidate in (json.loads(job["decision"] or "{}") or {}).get("candidates") or []:
                if not candidate.get("reasons") and not candidate.get("unknown"):
                    name = (candidate.get("release") or {}).get("provider")
                    if name in stats:
                        stats[name]["fit"] += 1
            chosen = (json.loads(job["candidate"] or "{}") or {}).get("release") or {}
            if chosen.get("provider") in stats:
                stats[chosen["provider"]]["chosen"] += 1
        return sorted(stats.values(), key=lambda entry: entry["provider"])

    @app.post("/admin/jobs/{job_id}/cancel")
    def cancel(job_id: int):
        job = db.job(job_id)
        if job["replacing"]:
            # The request itself was fulfilled; cancelling stops only the replacement.
            abort_upgrade(job_id)
            return {"status": "AVAILABLE", "upgrade_cancelled": True}
        db.cancel_from_admin(job_id)
        return {"status": "CANCELLED", "torrent_retained": True, "pause_pending": bool(job["torrent_hash"])}

    @app.post("/events/seerr")
    def webhook():
        return {"reconciled": seerr.reconcile(db, settings.dry_run)}

    @app.get("/metrics")
    @app.get("/admin/metrics")
    def metrics():
        return {
            "queue": db.execute("SELECT state,COUNT(*) count FROM jobs GROUP BY state"),
            "llm": db.execute("SELECT day,COUNT(*) calls,SUM(tokens) tokens FROM llm_usage GROUP BY day"),
            "llm_outcomes": db.execute(
                "SELECT day,outcome,COUNT(*) calls,SUM(charged_tokens) charged_tokens,"
                "AVG(latency_ms) latency_ms FROM llm_usage GROUP BY day,outcome"
            ),
            "imports": db.execute("SELECT state,COUNT(*) count FROM imports GROUP BY state"),
        }

    def approved_work(kind, external_id):
        approved = []
        source_requests = []
        for req in seerr.approved():
            req_kind = "movie" if req["type"] == "movie" else "series"
            if (
                req_kind == kind
                and req["media"].get("tmdbId" if kind == "movie" else "tvdbId") == external_id
            ):
                approved.append(seerr.work(req))
                source_requests.append(req)
        if not approved:
            raise HTTPException(409, "No approved Seerr request for this work")
        return approved[-1].model_copy(
            update={
                "seasons": sorted({s for work in approved for s in work.seasons}),
                "episodes": sorted({ep for work in approved for ep in work.episodes}),
            }
        ), source_requests

    def render(work, job=None):
        imports = (
            db.execute("SELECT * FROM imports WHERE job_id=:id AND state='PUBLISHED'", id=job["id"])
            if job
            else []
        )
        covered = set()
        for item in imports:
            covered.update((item["episode"] or "").split("+"))
        if work.kind == "series":
            covered = (
                {
                    e["episode"]
                    for e in db.execute(
                        "SELECT episode FROM episodes WHERE job_id=:id AND available=1", id=job["id"]
                    )
                }
                if job
                else set()
            )
        result = {
            "title": work.title,
            "year": work.year,
            "qualityProfileId": 1,
            "profileId": 1,
            "tags": db.tags_for_job(job["id"]) if job else [],
            "monitored": not job or job["state"] != "CANCELLED",
            "titleSlug": str(work.external_id),
            "hasFile": bool(job and job["state"] == "AVAILABLE"),
        }
        if job:
            result["id"] = job["id"]
        if work.kind == "movie":
            result.update(
                tmdbId=work.external_id,
                isAvailable=result["hasFile"],
                path=imports[0]["destination"] if imports else "",
            )
        else:
            seasons = []
            for s in work.seasons:
                wanted = [ep for ep in work.episodes if ep.startswith(f"S{s:02d}E")]
                count = len(set(wanted) & covered)
                seasons.append(
                    {
                        "seasonNumber": s,
                        "monitored": True,
                        "statistics": {
                            "episodeFileCount": count,
                            "episodeCount": len(wanted),
                            "totalEpisodeCount": len(wanted),
                            "percentOfEpisodes": 100 * count / len(wanted) if wanted else 0,
                        },
                    }
                )
            result.update(
                tvdbId=work.external_id,
                seasons=seasons,
                statistics={
                    "episodeFileCount": len(set(work.episodes) & covered),
                    "episodeCount": len(work.episodes),
                    "totalEpisodeCount": len(work.episodes),
                },
                seriesType="standard",
                seasonFolder=True,
            )
        return result

    def register(kind):
        prefix = "/radarr/api/v3" if kind == "movie" else "/sonarr/api/v3"
        endpoint = "movie" if kind == "movie" else "series"

        @app.get(prefix + "/system/status")
        def status():
            return {
                "version": "0.1.0",
                "appName": "Media Acquisition Engine",
                "instanceName": "MAE",
                "urlBase": "/radarr" if kind == "movie" else "/sonarr",
                "isProduction": True,
                "isLinux": True,
                "authentication": "apiKey",
            }

        @app.get(prefix + "/qualityProfile")
        def quality_profiles():
            return [{"id": 1, "name": settings.policy.version, "upgradeAllowed": False}]

        @app.get(prefix + "/rootfolder")
        def roots():
            path = settings.store / ("films" if kind == "movie" else "serials")
            usage = shutil.disk_usage(path)
            return [
                {
                    "id": 1,
                    "path": str(path),
                    "freeSpace": usage.free,
                    "totalSpace": usage.total,
                    "unmappedFolders": [],
                }
            ]

        @app.get(prefix + "/tag")
        def tags():
            return db.tags()

        def valid_label(payload):
            label = payload.get("label") if isinstance(payload, dict) else None
            if (
                not isinstance(label, str)
                or not (1 <= len(label.strip()) <= 100)
                or any(ord(c) < 32 for c in label)
            ):
                raise HTTPException(422, "Tag label must be 1-100 printable characters")
            return label.strip()

        @app.post(prefix + "/tag", status_code=201)
        def create_tag(payload: dict):
            try:
                return db.create_tag(valid_label(payload))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

        @app.put(prefix + "/tag/{tag_id}")
        def update_tag(tag_id: int, payload: dict):
            try:
                return db.update_tag(tag_id, valid_label(payload))
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

        @app.delete(prefix + "/tag/{tag_id}", status_code=204)
        def delete_tag(tag_id: int):
            try:
                db.delete_tag(tag_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None

        @app.get(prefix + "/languageprofile")
        def language():
            return [{"id": 1, "name": "Russian audio required"}]

        @app.get(prefix + "/" + endpoint + "/lookup")
        def lookup(term: str):
            namespace = "tmdb:" if kind == "movie" else "tvdb:"
            if not term.startswith(namespace) or not term[len(namespace) :].isdigit():
                raise HTTPException(422, "External ID lookup required")
            work, _ = approved_work(kind, int(term[len(namespace) :]))
            existing = db.execute("SELECT * FROM jobs WHERE identity=:key", key=f"{kind}:{work.external_id}")
            return [render(work, existing[0] if existing else None)]

        @app.get(prefix + "/" + endpoint)
        def listing():
            return [
                render(Work.model_validate_json(j["work"]), j)
                for j in db.jobs()
                if j["identity"].startswith(kind + ":")
            ]

        @app.get(prefix + "/" + endpoint + "/{job_id}")
        def get_one(job_id: int):
            j = db.job(job_id)
            if not j["identity"].startswith(kind + ":"):
                raise HTTPException(404)
            return render(Work.model_validate_json(j["work"]), j)

        @app.post(prefix + "/" + endpoint)
        @app.put(prefix + "/" + endpoint)
        def add(payload: dict):
            work, source_requests = approved_work(kind, payload.get("tmdbId" if kind == "movie" else "tvdbId"))
            if payload.get("qualityProfileId") != 1:
                raise HTTPException(422, "Unknown quality profile")
            expected_root = str(settings.store / ("films" if kind == "movie" else "serials"))
            if payload.get("rootFolderPath", expected_root) != expected_root:
                raise HTTPException(422, "Invalid root folder")
            if kind == "series":
                if payload.get("seriesType", "standard") != "standard":
                    raise HTTPException(422, "Unsupported numbering scheme")
                seasons = [s["seasonNumber"] for s in payload.get("seasons", []) if s.get("monitored")]
                if not seasons or not set(seasons).issubset(work.seasons):
                    raise HTTPException(409, "Season not approved")
                work = work.model_copy(
                    update={
                        "seasons": seasons,
                        "episodes": [
                            ep for ep in work.episodes if any(ep.startswith(f"S{s:02d}E") for s in seasons)
                        ],
                    }
                )
            job_id = db.submit(work, settings.dry_run)
            for request in source_requests:
                if type(request.get("id")) is int:
                    db.observe_seerr_request(request["id"], job_id, request.get("status"))
            if "tags" in payload:
                try:
                    db.set_job_tags(job_id, payload["tags"])
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
            return render(work, db.job(job_id))

        @app.delete(prefix + "/" + endpoint + "/{job_id}")
        def remove(job_id: int, deleteFiles: bool = False):
            if deleteFiles:
                raise HTTPException(409, "Library deletion not supported")
            get_one(job_id)
            return cancel(job_id)

        @app.post(prefix + "/command")
        def command(payload: dict):
            if payload.get("name") not in {"MoviesSearch", "SeriesSearch", "SeasonSearch"}:
                raise HTTPException(422, "Unsupported command")
            ids = payload.get("movieIds", []) if kind == "movie" else [payload.get("seriesId")]
            if not ids or any(i is None for i in ids):
                raise HTTPException(422, "Explicit work IDs required")
            for job_id in ids:
                get_one(job_id)
                if db.job(job_id)["state"] not in {
                    "AVAILABLE",
                    "CANCELLED",
                    "DOWNLOADING",
                    "IMPORTING",
                    "VERIFYING",
                }:
                    db.transition(job_id, "REQUESTED")
            return {"name": payload["name"], "status": "queued", "workIds": ids}

        @app.get(prefix + "/queue")
        def queue(page: int = 1, pageSize: int = 20):
            if page < 1 or pageSize < 1 or pageSize > 100:
                raise HTTPException(422)
            rows = [
                j
                for j in db.jobs()
                if j["identity"].startswith(kind + ":")
                and j["state"] in {"DOWNLOADING", "VERIFYING", "IMPORTING"}
            ]
            records = [
                {
                    "id": j["id"],
                    "movieId" if kind == "movie" else "seriesId": j["id"],
                    "title": json.loads(j["work"])["title"],
                    "downloadId": j["torrent_hash"],
                    "trackedDownloadState": j["state"],
                    "status": j["state"],
                    "protocol": "torrent",
                }
                for j in rows
            ]
            return {
                "page": page,
                "pageSize": pageSize,
                "totalRecords": len(records),
                "records": records[(page - 1) * pageSize : page * pageSize],
            }

    register("movie")
    register("series")

    def episode_rows(job_id):
        job = db.job(job_id)
        if not job["identity"].startswith("series:"):
            raise HTTPException(404, "Series not found")
        return [
            {
                "id": row["id"],
                "seriesId": job_id,
                "seasonNumber": int(row["episode"].split("E")[0][1:]),
                "episodeNumber": int(row["episode"].split("E")[1]),
                    "monitored": bool(row["monitored"]) and job["state"] != "CANCELLED",
                "hasFile": bool(row["available"]),
            }
            for row in db.execute("SELECT * FROM episodes WHERE job_id=:id ORDER BY episode", id=job_id)
        ]

    @app.get("/sonarr/api/v3/episode")
    def episodes(seriesId: int):
        return episode_rows(seriesId)

    @app.put("/sonarr/api/v3/episode/monitor")
    def monitor_episodes(payload: dict):
        ids = payload.get("episodeIds")
        if (
            type(payload.get("monitored")) is not bool
            or not isinstance(ids, list)
            or not ids
            or len(ids) > 1000
            or any(type(i) is not int or i < 1 for i in ids)
        ):
            raise HTTPException(422, "Episode monitoring requires positive IDs")
        rows = []
        for episode_id in set(ids):
            result = db.execute("SELECT * FROM episodes WHERE id=:id", id=episode_id)
            if not result:
                raise HTTPException(404, "Episode not found")
            rows.extend(result)
        for job_id in {row["job_id"] for row in rows}:
            job = db.job(job_id)
            if job["state"] == "CANCELLED":
                raise HTTPException(409, "Cancelled request requires a new approved request")
            if payload["monitored"]:
                work = Work.model_validate_json(job["work"])
                approved, _ = approved_work("series", work.external_id)
                if not {r["episode"] for r in rows if r["job_id"] == job_id}.issubset(approved.episodes):
                    raise HTTPException(409, "Episode no longer approved")
        db.set_episode_monitoring(ids, payload["monitored"])
        return [
            episode
            for job_id in sorted({r["job_id"] for r in rows})
            for episode in episode_rows(job_id)
            if episode["id"] in ids
        ]

    return app
