import httpx

from mae.domain import Work


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
            response = client.get("/api/v1" + path, params=params)
            response.raise_for_status()
            return response.json()

    def approved(self):
        rows = []
        for page in range(100):
            data = self.get("/request", take=100, skip=page * 100)
            batch = data["results"]
            rows.extend(r for r in batch if r.get("status") == 2)
            if len(batch) < 100:
                return rows
        raise RuntimeError("SEERR_PAGINATION_LIMIT")

    def work(self, request):
        media = request["media"]
        kind = "movie" if request["type"] == "movie" else "series"
        tmdb = media["tmdbId"]
        detail = self.get(f"/{'movie' if kind == 'movie' else 'tv'}/{tmdb}")
        seasons = [s["seasonNumber"] for s in request.get("seasons", []) if s.get("status") == 2]
        episodes = []
        if kind == "series":
            for season in seasons:
                season_data = self.get(f"/tv/{tmdb}/season/{season}")
                episodes.extend(f"S{season:02d}E{ep['episodeNumber']:02d}" for ep in season_data["episodes"])
        date = detail.get("releaseDate") or detail.get("firstAirDate") or ""
        return Work(
            kind=kind,
            external_id=tmdb if kind == "movie" else media["tvdbId"],
            title=detail.get("title") or detail["name"],
            aliases=[v for v in [detail.get("originalTitle"), detail.get("originalName")] if v],
            year=int(date[:4]) if date else None,
            original_language=detail.get("originalLanguage"),
            seasons=seasons,
            episodes=episodes,
        )

    def reconcile(self, db, dry_run):
        # Repeated webhook/facade/poll events converge on the same external identity.
        # Missing requests do not imply cancellation: availability can remove approval.
        count = 0
        for request in self.approved():
            db.submit(self.work(request), dry_run)
            count += 1
        return count
