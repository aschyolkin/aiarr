import re
import time

import httpx

from mae.domain import TMDB_SERIES_OFFSET, Work


def poster(detail):
    value = detail.get("posterPath")
    return value if isinstance(value, str) and re.fullmatch(r"/[A-Za-z0-9_.-]{1,199}", value) else None


# Seerr proxies TMDB; reconciliation runs every minute for every approved
# request, but titles and episode lists change on a scale of hours.
_METADATA_CACHE = {}
METADATA_TTL = 3600
# Trackers name foreign films by their English title, not e.g. Korean script.
ENGLISH_TITLE_LANGUAGES = {"en", "ru"}


def series_id(media):
    """The TVDB id Seerr holds for a series, or the TMDB-based stand-in when there is none."""
    return media.get("tvdbId") or TMDB_SERIES_OFFSET + media["tmdbId"]


class Seerr:
    def __init__(self, settings):
        self.settings = settings

    def get(self, path, **params):
        key = self.settings.seerr_key.get_secret_value()
        if not key:
            raise RuntimeError("SEERR_KEY_REQUIRED")
        with httpx.Client(
            base_url=self.settings.seerr_url,
            timeout=20,
            trust_env=False,
            follow_redirects=False,
            headers={"X-Api-Key": key},
        ) as client:
            response = client.get("/api/v1" + path, params=params or None)  # keeps a hand-encoded query
            response.raise_for_status()
            return response.json()

    def requests(self):
        rows = []
        for page in range(100):
            data = self.get("/request", take=100, skip=page * 100)
            batch = data["results"]
            rows.extend(batch)
            if len(batch) < 100:
                return rows
        raise RuntimeError("SEERR_PAGINATION_LIMIT")

    def approved(self):
        return [request for request in self.requests() if request.get("status") == 2]

    def request_status(self, request_id):
        try:
            return self.get(f"/request/{request_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    def metadata(self, path, **params):
        key = (self.settings.seerr_url, path, tuple(sorted(params.items())))
        cached = _METADATA_CACHE.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        data = self.get(path, **params)
        if len(_METADATA_CACHE) >= 512:
            _METADATA_CACHE.clear()
        _METADATA_CACHE[key] = (time.monotonic() + METADATA_TTL, data)
        return data

    def work(self, request):
        media = request["media"]
        kind = "movie" if request["type"] == "movie" else "series"
        tmdb = media["tmdbId"]
        path = f"/{'movie' if kind == 'movie' else 'tv'}/{tmdb}"
        detail = self.metadata(path)
        seasons = [s["seasonNumber"] for s in request.get("seasons", []) if s.get("status") == 2]
        episodes, air_dates = [], {}
        if kind == "series":
            for season in seasons:
                season_data = self.metadata(f"/tv/{tmdb}/season/{season}")
                for ep in season_data["episodes"]:
                    key = f"S{season:02d}E{ep['episodeNumber']:02d}"
                    episodes.append(key)
                    date = ep.get("airDate")
                    air_dates[key] = date if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else ""
        date = detail.get("releaseDate") or detail.get("firstAirDate") or ""
        title = detail.get("title") or detail["name"]
        aliases = [v for v in [detail.get("originalTitle"), detail.get("originalName")] if v]
        language = detail.get("originalLanguage")
        if language and language not in ENGLISH_TITLE_LANGUAGES:
            try:
                english = self.metadata(path, language="en")
                value = english.get("title") or english.get("name")
                if isinstance(value, str) and value.strip() and value not in (title, *aliases):
                    aliases.append(value.strip()[:250])
            except httpx.HTTPError:
                pass  # An extra search name only; never blocks the request.
        return Work(
            kind=kind,
            external_id=tmdb if kind == "movie" else series_id(media),
            title=title,
            aliases=aliases,
            year=int(date[:4]) if date else None,
            original_language=language,
            seasons=seasons,
            episodes=episodes,
            air_dates=air_dates,
            poster_path=poster(detail),
        )

    def backfill_poster(self, db, request):
        """Jobs created before posters were stored get one TMDB lookup."""
        if type(request.get("id")) is not int:
            return
        rows = db.execute(
            "SELECT job_id FROM seerr_requests r JOIN jobs j ON j.id=r.job_id WHERE r.request_id=:id "
            "AND json_extract(j.work,'$.poster_path') IS NULL", id=request["id"],
        )
        if not rows:
            return
        try:
            media = request["media"]
            detail = self.get(f"/{'movie' if request['type'] == 'movie' else 'tv'}/{media['tmdbId']}")
        except Exception:
            return  # Display-only data; never blocks reconciliation.
        if value := poster(detail):
            db.set_poster(rows[0]["job_id"], value)

    def reconcile(self, db, dry_run):
        # Repeated webhook/facade/poll events converge on the same request identity.
        # Missing requests do not imply cancellation: availability can remove approval.
        count = 0
        requests = self.requests()
        seen = set()
        for request in requests:
            request_id = request.get("id")
            if type(request_id) is int:
                seen.add(request_id)
            if request.get("status") == 2:
                job_id = db.submit(self.work(request), dry_run)
                db.observe_seerr_request(request_id, job_id, request["status"])
                count += 1
            elif type(request_id) is int:
                rows = db.execute("SELECT job_id FROM seerr_requests WHERE request_id=:id", id=request_id)
                if rows:
                    db.observe_seerr_request(request_id, rows[0]["job_id"], request.get("status"))
            self.backfill_poster(db, request)
        # A list can omit a record because of Seerr filtering. Confirm each
        # disappearance through its own endpoint before deleting any file.
        for row in db.execute("SELECT request_id,job_id FROM seerr_requests WHERE status!=-1"):
            if row["request_id"] in seen:
                continue
            record = self.request_status(row["request_id"])
            if record is None:
                db.mark_seerr_request_deleted(row["request_id"])
            elif type(record.get("status")) is int:
                db.observe_seerr_request(row["request_id"], row["job_id"], record["status"])
        return count
