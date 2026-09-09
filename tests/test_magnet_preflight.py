from unittest.mock import Mock

from mae.downloads import Downloader


def test_magnet_preflight_stops_at_metadata_and_never_resumes(settings):
    downloader = Downloader(settings)
    row = {"category": "mae", "size": 123, "state": "stoppedDL"}
    downloader.client = Mock()
    downloader.client.torrents_info.side_effect = [[], [row], [row]]
    downloader.client.torrents_categories.return_value = {"mae": {}}
    downloader.client.torrents_files.return_value = [{"name": "Safe.Movie.mkv"}]
    url = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"

    torrent_hash, size, files = downloader.magnet_preflight(url, "movie", timeout=1)

    assert torrent_hash == "0123456789abcdef0123456789abcdef01234567"
    assert (size, files) == (123, ["Safe.Movie.mkv"])
    downloader.client.torrents_add.assert_called_once()
    assert downloader.client.torrents_add.call_args.kwargs["stop_condition"] == "MetadataReceived"
    downloader.client.torrents_resume.assert_not_called()


def test_magnet_preflight_rejects_unowned_existing_torrent(settings):
    downloader = Downloader(settings)
    downloader.client = Mock()
    downloader.client.torrents_info.return_value = [{"category": "other"}]
    url = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"

    try:
        downloader.magnet_preflight(url, "movie", timeout=1)
    except ValueError as exc:
        assert str(exc) == "TORRENT_OWNED_BY_ANOTHER_SERVICE"
    else:
        raise AssertionError("foreign torrent must not be preflighted")
