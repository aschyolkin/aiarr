from pathlib import Path

import httpx

from mae.verification import check_path


class Jellyfin:
    def __init__(self, settings):
        self.settings = settings
        self.items = None

    def prime(self):
        """List every movie and series once; coverage() then needs no query per work."""
        with self.client() as client:
            response = client.get(
                "/Items",
                params={"Recursive": "true", "Fields": "ProviderIds,Path", "IncludeItemTypes": "Movie,Series"},
            )
            response.raise_for_status()
            self.items = response.json().get("Items", [])

    def client(self):
        key = self.settings.jellyfin_key.get_secret_value()
        if not key:
            raise ValueError("JELLYFIN_KEY_REQUIRED")
        return httpx.Client(
            base_url=self.settings.jellyfin_url,
            headers={"Authorization": f'MediaBrowser Token="{key}"'},  # X-Emby-Token is rejected since Jellyfin 12
            timeout=20,
            trust_env=False,
            follow_redirects=False,
        )

    def coverage(self, work):
        namespace = "Tmdb" if work.kind == "movie" else "Tvdb"
        kind = "Movie" if work.kind == "movie" else "Series"
        with self.client() as client:
            if self.items is not None:
                listed = [item for item in self.items if item.get("Type") == kind]
            else:
                response = client.get(
                    "/Items",
                    params={
                        "Recursive": "true",
                        "Fields": "ProviderIds,Path",
                        "AnyProviderIdEquals": f"{namespace}.{work.external_id}",
                        "IncludeItemTypes": kind,
                    },
                )
                response.raise_for_status()
                listed = response.json().get("Items", [])
            # Verify IDs ourselves as well: an ignored filter must not mean available.
            items = [
                item
                for item in listed
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

    def existing_series_folder(self, work):
        if work.kind != "series":
            return None
        with self.client() as client:
            response = client.get(
                "/Items",
                params={
                    "Recursive": "true", "Fields": "ProviderIds,Path",
                    "AnyProviderIdEquals": f"Tvdb.{work.external_id}",
                    "IncludeItemTypes": "Series",
                },
            )
            response.raise_for_status()
        paths = {
            item.get("Path")
            for item in response.json().get("Items", [])
            if str(item.get("ProviderIds", {}).get("Tvdb")) == str(work.external_id)
            and isinstance(item.get("Path"), str) and item.get("Path")
        }
        if len(paths) > 1:
            raise ValueError("AMBIGUOUS_LIBRARY_SERIES_FOLDER")
        if not paths:
            return None
        folder = Path(next(iter(paths)))
        if folder.parent != self.settings.store / "serials" or not folder.is_dir():
            raise ValueError("LIBRARY_SERIES_PATH_UNSAFE")
        check_path(folder, self.settings.store)
        return folder

    def refresh(self):
        with self.client() as client:
            client.post("/Library/Refresh").raise_for_status()
