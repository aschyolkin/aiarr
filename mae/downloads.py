import base64
import hashlib
import time
from pathlib import PurePosixPath
from urllib.parse import parse_qs, urlsplit

import bencodepy
import qbittorrentapi
from guessit import guessit

from mae.domain import Outcome, ProviderError
from mae.parsing import episode_keys


def safe_relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise ValueError("UNSAFE_TORRENT_PATH")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError("HIDDEN_TORRENT_PATH")
    return path


def torrent_metadata(raw, with_sizes=False):
    if len(raw) > 2_000_000:
        raise ValueError("TORRENT_TOO_LARGE")
    try:
        data = bencodepy.decode(raw)
        info = data[b"info"]
        if b"pieces" not in info:  # v2/hybrid need explicit support, not guessed hashes.
            raise ValueError("UNSUPPORTED_TORRENT_VERSION")
        root = info[b"name"].decode("utf-8")
        safe_relative(root)
        files = info.get(b"files", [{b"path": [], b"length": info.get(b"length")}])
        total = 0
        names = []
        if len(files) > 10000:
            raise ValueError("TOO_MANY_FILES")
        for f in files:
            name = "/".join([root, *(p.decode("utf-8") for p in f[b"path"])])
            safe_relative(name)
            length = f[b"length"]
            if not isinstance(length, int) or length < 0:
                raise ValueError("INVALID_SIZE")
            total += length
            names.append((name, length) if with_sizes else name)
        # Canonical bencoding is required so computed hash is the original infohash.
        if bencodepy.encode(data) != raw:
            raise ValueError("NONCANONICAL_TORRENT")
        return hashlib.sha1(bencodepy.encode(info)).hexdigest(), total, names
    except (KeyError, TypeError, UnicodeError, bencodepy.DecodingError):
        raise ValueError("INVALID_TORRENT") from None


def magnet_infohash(url):
    """Return canonical v1 infohash; v2/hybrid magnets need explicit support."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "magnet":
        raise ValueError("INVALID_MAGNET")
    values = parse_qs(parsed.query, keep_blank_values=True).get("xt", [])
    matches = [value[9:] for value in values if value.lower().startswith("urn:btih:")]
    if len(matches) != 1:
        raise ValueError("INVALID_MAGNET")
    value = matches[0]
    if len(value) == 40 and all(ch in "0123456789abcdefABCDEF" for ch in value):
        return value.lower()
    if len(value) == 32:
        try:
            decoded = base64.b32decode(value.upper())
        except ValueError:
            raise ValueError("INVALID_MAGNET") from None
        if len(decoded) == 20:
            return decoded.hex()
    raise ValueError("UNSUPPORTED_MAGNET_VERSION")


class NoReplayClient(qbittorrentapi.Client):
    """Pinned SDK forces at least one manager retry; bypass it for torrent adds.

    Other calls retain the upstream client behavior. This small override must be
    re-audited whenever qbittorrent-api changes its private request interface.
    """

    def _request_manager(self, **kwargs):
        if kwargs.get("api_namespace") == "torrents" and kwargs.get("api_method") == "add":
            for key in ("version_introduced", "version_removed", "_retries", "_retry_backoff_factor"):
                kwargs.pop(key, None)
            return self._request(**kwargs)
        return super()._request_manager(**kwargs)


class Downloader:
    category = "mae"
    selection_tag = "mae-selection-pending"

    def __init__(self, settings):
        self.settings = settings
        self.client = NoReplayClient(
            host=settings.qbit_url,
            username=settings.qbit_username,
            password=settings.qbit_password.get_secret_value(),
            REQUESTS_ARGS={"timeout": 20},
            HTTPADAPTER_ARGS={"max_retries": 0},
            DISABLE_LOGGING_DEBUG_OUTPUT=True,
            SIMPLE_RESPONSES=True,
        )

    def info(self, torrent_hash):
        rows = self.client.torrents_info(torrent_hashes=torrent_hash)
        if not rows:
            return None
        row = rows[0]
        if row["category"] != self.category:
            raise ValueError("TORRENT_OWNED_BY_ANOTHER_SERVICE")
        return row

    def add(self, raw, torrent_hash, kind, wanted_episodes=None, download_limit=None):
        if self.info(torrent_hash):
            if download_limit:
                self.set_download_limit(torrent_hash, download_limit)
            if kind == "series":
                self.complete_selection(torrent_hash, wanted_episodes)
            return
        categories = self.client.torrents_categories()
        if self.category not in categories:
            self.client.torrents_create_category(name=self.category)
        folder = "films" if kind == "movie" else "serials"
        result = self.client.torrents_add(
            torrent_files=raw,
            category=self.category,
            save_path=f"/data/store/downloads/{folder}",
            tags=(
                f"media-acquisition-engine,{self.selection_tag}"
                if kind == "series" else "media-acquisition-engine"
            ),
            is_stopped=True if kind == "series" else None,
            use_auto_torrent_management=False,
            download_limit=download_limit,
        )
        # Newer qBittorrent versions can return structured add metadata.
        # Reconcile actual client state rather than assuming one response string.
        if result != "Ok." and not self.info(torrent_hash):
            raise ProviderError(Outcome.TEMPORARY_FAILURE)
        if kind == "series":
            self.complete_selection(torrent_hash, wanted_episodes)

    def complete_selection(self, torrent_hash, wanted_episodes):
        info = self.info(torrent_hash)
        if info is None:
            raise ProviderError(Outcome.TEMPORARY_FAILURE)
        if self.selection_tag not in {tag.strip() for tag in info.get("tags", "").split(",")}:
            raise ValueError("TORRENT_SELECTION_MARKER_MISSING")
        if not wanted_episodes or not all(
            isinstance(key, str) and len(key) == 6 and key[:1] == "S" and key[3:4] == "E"
            and key[1:3].isdigit() and key[4:].isdigit() for key in wanted_episodes
        ):
            raise ValueError("TORRENT_SELECTION_INTENT_MISSING")
        wanted = set(wanted_episodes)
        # qBittorrent may expose the torrent row before its file table.
        deadline = time.monotonic() + 10
        files = self.files(torrent_hash)
        while not files and time.monotonic() < deadline:
            time.sleep(0.5)
            files = self.files(torrent_hash)
        if not files or len(files) > 10000:
            raise ProviderError(Outcome.TEMPORARY_FAILURE)
        chosen = set()
        priorities = {}
        for row in files:
            path = safe_relative(row["name"])
            keys = set(episode_keys(guessit(str(path)))) if path.suffix.lower() in {
                ".mkv", ".mp4", ".m4v", ".avi", ".ts"
            } else set()
            if keys & wanted and not keys.issubset(wanted):
                raise ValueError("TORRENT_FILE_COVERS_UNMONITORED_EPISODE")
            selected = bool(keys & wanted)
            if selected:
                chosen.update(keys)
            priorities[row["index"]] = 1 if selected else 0
        if not chosen:
            raise ValueError("TORRENT_OVERLAPS_EXISTING_OR_UNREQUESTED_EPISODES")
        for index, priority in priorities.items():
            self.client.torrents_file_priority(torrent_hash=torrent_hash, file_ids=index, priority=priority)
        verified = self.files(torrent_hash)
        if len(verified) != len(files) or any(
            row["index"] not in priorities or row["priority"] != priorities[row["index"]]
            for row in verified
        ):
            raise ProviderError(Outcome.TEMPORARY_FAILURE)
        self.resume(torrent_hash)
        self.client.torrents_remove_tags(tags=self.selection_tag, torrent_hashes=torrent_hash)

    def magnet_preflight(self, url, kind, timeout=30, with_sizes=False):
        """Obtain magnet metadata without allowing payload download to start."""
        torrent_hash = magnet_infohash(url)
        info = self.info(torrent_hash)
        if info is None:
            categories = self.client.torrents_categories()
            if self.category not in categories:
                self.client.torrents_create_category(name=self.category)
            folder = "films" if kind == "movie" else "serials"
            self.client.torrents_add(
                urls=url,
                category=self.category,
                save_path=f"/data/store/downloads/{folder}",
                tags=(
                    f"media-acquisition-engine,mae-preflight,{self.selection_tag}"
                    if kind == "series" else "media-acquisition-engine,mae-preflight"
                ),
                use_auto_torrent_management=False,
                stop_condition="MetadataReceived",
            )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            info = self.info(torrent_hash)
            if info:
                files = self.files(torrent_hash)
                if files:
                    size = info.get("size") or info.get("total_size")
                    if isinstance(size, int) and size >= 0:
                        return torrent_hash, size, [
                            (row["name"], row.get("size")) if with_sizes else row["name"] for row in files
                        ]
            time.sleep(0.5)
        raise ValueError("MAGNET_METADATA_UNAVAILABLE")

    def resume(self, torrent_hash):
        self.info(torrent_hash)  # ownership check before modifying state
        self.client.torrents_resume(torrent_hashes=torrent_hash)

    def set_download_limit(self, torrent_hash, limit):
        """Bytes per second; -1 removes the per-torrent limit."""
        self.info(torrent_hash)  # ownership check before modifying state
        self.client.torrents_set_download_limit(limit=limit, torrent_hashes=torrent_hash)

    def files(self, torrent_hash):
        self.info(torrent_hash)  # ownership check before accessing paths
        return self.client.torrents_files(torrent_hash=torrent_hash)

    def hashes_for_sources(self, sources, downloads_root):
        """Recover legacy ownership only from exact journaled download paths."""
        from pathlib import Path

        from mae.verification import check_path

        wanted = {Path(source) for source in sources}
        for path in wanted:
            check_path(path, downloads_root)
        found = set()
        for row in self.client.torrents_info(category=self.category):
            if row.get("category") != self.category or "media-acquisition-engine" not in {
                tag.strip() for tag in row.get("tags", "").split(",")
            }:
                continue
            save_path = Path(row.get("save_path") or "")
            check_path(save_path, downloads_root)
            for file in self.client.torrents_files(torrent_hash=row["hash"]):
                path = save_path / str(safe_relative(file["name"]))
                check_path(path, downloads_root)
                if path in wanted:
                    found.add(row["hash"])
        return found

    def delete_owned(self, torrent_hash, downloads_root):
        """Delete torrent and payload only after category, tag and path checks."""
        info = self.info(torrent_hash)
        if info is None:
            return False
        if "media-acquisition-engine" not in {tag.strip() for tag in info.get("tags", "").split(",")}:
            raise ValueError("TORRENT_OWNERSHIP_TAG_MISSING")
        save_path = PurePosixPath(info.get("save_path") or "")
        if not save_path.is_absolute() or ".." in save_path.parts or not save_path.is_relative_to(
            PurePosixPath(str(downloads_root))
        ):
            raise ValueError("TORRENT_OUTSIDE_MANAGED_PATH")
        for row in self.client.torrents_files(torrent_hash=torrent_hash):
            safe_relative(row["name"])
        self.client.torrents_delete(delete_files=True, torrent_hashes=torrent_hash)
        if self.client.torrents_info(torrent_hashes=torrent_hash):
            raise ProviderError(Outcome.TEMPORARY_FAILURE)
        return True

    def apply_speed_limits(self, limited, download, upload):
        """Global alternative limits (bytes/s), switched on only while limited.

        qBittorrent's own scheduler runs in the container's time zone, so
        aiarr switches the mode itself and keeps that scheduler off.
        """
        prefs = self.client.app_preferences()
        wanted = {"alt_dl_limit": download, "alt_up_limit": upload, "scheduler_enabled": False}
        changes = {key: value for key, value in wanted.items() if prefs.get(key) != value}
        if changes:
            self.client.app_set_preferences(prefs=changes)
        if self.speed_limited() != limited:
            self.client.transfer_set_speed_limits_mode(intended_state=limited)

    def speed_limited(self):
        return str(self.client.transfer_speed_limits_mode()) == "1"

    def transfer(self):
        info = self.client.transfer_info()
        prefs = self.client.app_preferences()
        return {
            "limited": self.speed_limited(),
            "download_speed": int(info.get("dl_info_speed") or 0),
            "upload_speed": int(info.get("up_info_speed") or 0),
            "alt_download_limit": int(prefs.get("alt_dl_limit") or 0),
            "alt_upload_limit": int(prefs.get("alt_up_limit") or 0),
        }

    def pause(self, torrent_hash):
        """Pause only a torrent that remains demonstrably owned by this engine."""
        if self.info(torrent_hash) is None:
            return False
        self.client.torrents_stop(torrent_hashes=torrent_hash)
        return True
