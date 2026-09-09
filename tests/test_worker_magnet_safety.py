from unittest.mock import Mock

from mae.domain import DownloadHandle, ReleaseDetails, SearchResult
from mae.worker import Worker


class MagnetProvider:
    config = Mock(name="magnet")

    def search(self, work):
        return [SearchResult(provider="magnet", release_id="1", title=work.title)]

    def fetch_details(self, release):
        return ReleaseDetails()

    def resolve_download(self, release):
        return DownloadHandle(url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567")


def test_magnet_path_is_checked_before_resume(settings, db, movie, monkeypatch):
    provider = MagnetProvider()
    downloader = Mock()
    downloader.magnet_preflight.return_value = ("a" * 40, 1, ["../escape.mkv"])
    monkeypatch.setattr("mae.worker.evaluate", lambda candidate, *args: candidate)
    monkeypatch.setattr("mae.worker.Jellyfin", lambda _settings: Mock(coverage=Mock(return_value=set())))
    job_id = db.submit(movie, False)

    Worker(settings.model_copy(update={"dry_run": False}), db, [provider], downloader).tick()

    assert db.job(job_id)["state"] == "NEEDS_REVIEW"
    assert db.job(job_id)["reason"] == "UNSAFE_TORRENT_PATH"
    downloader.resume.assert_not_called()
