import json
import secrets
import shutil

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from mae.config import Settings
from mae.db import Database
from mae.domain import Work
from mae.seerr import Seerr


def create_app(settings=None, db=None, seerr=None):
    settings = settings or Settings()
    db = db or Database(settings.state_dir)
    seerr = seerr or Seerr(settings)

    def authenticate(request: Request):
        supplied = request.headers.get("X-Api-Key") or request.query_params.get("apikey") or ""
        if not secrets.compare_digest(supplied, settings.api_key.get_secret_value()):
            raise HTTPException(401, "API key required")

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
        return """<!doctype html><meta charset="utf-8"><title>Media Acquisition Engine</title>
        <h1>Media Acquisition Engine</h1><p>Administrative diagnostics</p>
        <input id="key" type="password" placeholder="API key"><button id="load">Refresh</button>
        <pre id="output"></pre><script>
        document.getElementById('load').onclick=async()=>{
          const r=await fetch('/admin/jobs',{headers:{'X-Api-Key':document.getElementById('key').value}});
          document.getElementById('output').textContent=JSON.stringify(await r.json(),null,2);
        };</script>"""

    @app.get("/admin/jobs")
    def jobs():
        return db.jobs()

    @app.get("/admin/jobs/{job_id}")
    def detail(job_id: int):
        return {
            "job": db.job(job_id),
            "imports": db.execute("SELECT * FROM imports WHERE job_id=:id", id=job_id),
            "events": db.execute("SELECT * FROM events WHERE job_id=:id", id=job_id),
        }

    @app.get("/admin/profiles")
    def profiles():
        return settings.policy

    @app.get("/admin/sources")
    def sources():
        return [
            {"name": p.name, "protocol": p.protocol, "details_configured": bool(p.details_selector)}
            for p in settings.providers
        ]

    @app.post("/admin/jobs/{job_id}/retry")
    def retry(job_id: int):
        if db.job(job_id)["state"] in {"AVAILABLE", "CANCELLED"}:
            raise HTTPException(409, "Re-request through Seerr; upgrades disabled")
        db.transition(job_id, "REQUESTED", attempts=0)
        return db.job(job_id)

    @app.post("/admin/jobs/{job_id}/cancel")
    def cancel(job_id: int):
        db.job(job_id)
        db.transition(job_id, "CANCELLED", "ADMIN_CANCELLED")
        return {"status": "CANCELLED", "torrent_retained": True}

    @app.post("/events/seerr")
    def webhook():
        return {"reconciled": seerr.reconcile(db, settings.dry_run)}

    @app.get("/metrics")
    def metrics():
        return {
            "queue": db.execute("SELECT state,COUNT(*) count FROM jobs GROUP BY state"),
            "llm": db.execute("SELECT day,COUNT(*) calls,SUM(tokens) tokens FROM llm_usage GROUP BY day"),
            "imports": db.execute("SELECT state,COUNT(*) count FROM imports GROUP BY state"),
        }

    def approved_work(kind, external_id):
        for req in seerr.approved():
            req_kind = "movie" if req["type"] == "movie" else "series"
            if (
                req_kind == kind
                and req["media"].get("tmdbId" if kind == "movie" else "tvdbId") == external_id
            ):
                return seerr.work(req)
        raise HTTPException(409, "No approved Seerr request for this work")

    def render(work, job=None):
        imports = (
            db.execute("SELECT * FROM imports WHERE job_id=:id AND state='PUBLISHED'", id=job["id"])
            if job
            else []
        )
        covered = set()
        for item in imports:
            covered.update((item["episode"] or "").split("+"))
        result = {
            "title": work.title,
            "year": work.year,
            "qualityProfileId": 1,
            "profileId": 1,
            "tags": [],
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
            return []

        @app.get(prefix + "/languageprofile")
        def language():
            return [{"id": 1, "name": "Russian audio required"}]

        @app.get(prefix + "/" + endpoint + "/lookup")
        def lookup(term: str):
            namespace = "tmdb:" if kind == "movie" else "tvdb:"
            if not term.startswith(namespace) or not term[len(namespace) :].isdigit():
                raise HTTPException(422, "External ID lookup required")
            work = approved_work(kind, int(term[len(namespace) :]))
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
            work = approved_work(kind, payload.get("tmdbId" if kind == "movie" else "tvdbId"))
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
    return app
