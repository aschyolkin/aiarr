import hashlib
from pathlib import PurePosixPath

import bencodepy
import qbittorrentapi

from mae.domain import Outcome, ProviderError


def safe_relative(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise ValueError("UNSAFE_TORRENT_PATH")
    if any(part.startswith(".") for part in path.parts):
        raise ValueError("HIDDEN_TORRENT_PATH")
    return path


def torrent_metadata(raw):
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
            names.append(name)
        # Canonical bencoding is required so computed hash is the original infohash.
        if bencodepy.encode(data) != raw:
            raise ValueError("NONCANONICAL_TORRENT")
        return hashlib.sha1(bencodepy.encode(info)).hexdigest(), total, names
    except (KeyError, TypeError, UnicodeError, bencodepy.DecodingError):
        raise ValueError("INVALID_TORRENT") from None


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

    def add(self, raw, torrent_hash, kind):
        if self.info(torrent_hash):
            return
        categories = self.client.torrents_categories()
        if self.category not in categories:
            self.client.torrents_create_category(name=self.category)
        folder = "films" if kind == "movie" else "serials"
        result = self.client.torrents_add(
            torrent_files=raw,
            category=self.category,
            save_path=f"/data/store/downloads/{folder}",
            tags="media-acquisition-engine",
            use_auto_torrent_management=False,
        )
        # Newer qBittorrent versions can return structured add metadata.
        # Reconcile actual client state rather than assuming one response string.
        if result != "Ok." and not self.info(torrent_hash):
            raise ProviderError(Outcome.TEMPORARY_FAILURE)

    def files(self, torrent_hash):
        self.info(torrent_hash)  # ownership check before accessing paths
        return self.client.torrents_files(torrent_hash=torrent_hash)

    def pause(self, torrent_hash):
        """Pause only a torrent that remains demonstrably owned by this engine."""
        if self.info(torrent_hash) is None:
            return False
        self.client.torrents_stop(torrent_hashes=torrent_hash)
        return True
