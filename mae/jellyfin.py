import httpx


class Jellyfin:
    def __init__(self, settings):
        self.settings = settings

    def client(self):
        key = self.settings.jellyfin_key.get_secret_value()
        if not key:
            raise ValueError("JELLYFIN_KEY_REQUIRED")
        return httpx.Client(
            base_url=self.settings.jellyfin_url,
            headers={"X-Emby-Token": key},
            timeout=20,
            trust_env=False,
            follow_redirects=False,
        )

    def coverage(self, work):
        namespace = "Tmdb" if work.kind == "movie" else "Tvdb"
        with self.client() as client:
            response = client.get(
                "/Items",
                params={
                    "Recursive": "true",
                    "Fields": "ProviderIds,Path",
                    "AnyProviderIdEquals": f"{namespace}.{work.external_id}",
                    "IncludeItemTypes": "Movie" if work.kind == "movie" else "Series",
                },
            )
            response.raise_for_status()
            # Verify IDs ourselves as well: an ignored filter must not mean available.
            items = [
                item
                for item in response.json().get("Items", [])
                if str(item.get("ProviderIds", {}).get(namespace)) == str(work.external_id)
            ]
            if work.kind == "movie":
                return (
                    {"movie"}
                    if any(item.get("Path") and not item.get("IsMissing", False) for item in items)
                    else set()
                )
            coverage = set()
            for series in items:
                episodes = client.get(f"/Shows/{series['Id']}/Episodes", params={"Fields": "Path"})
                episodes.raise_for_status()
                for episode in episodes.json().get("Items", []):
                    season, number = episode.get("ParentIndexNumber"), episode.get("IndexNumber")
                    end = episode.get("IndexNumberEnd") or number
                    if (
                        not episode.get("Path")
                        or episode.get("IsMissing", False)
                        or not isinstance(season, int)
                        or not isinstance(number, int)
                        or not isinstance(end, int)
                        or not number <= end <= number + 100
                    ):
                        continue
                    coverage.update(f"S{season:02d}E{ep:02d}" for ep in range(number, end + 1))
            return coverage

    def refresh(self):
        with self.client() as client:
            client.post("/Library/Refresh").raise_for_status()
