from unittest.mock import Mock

import pytest
import requests
from qbittorrentapi.definitions import APINames

from mae.downloads import Downloader


@pytest.mark.parametrize("namespace", ["torrents", APINames.Torrents])
def test_sdk_constructs_and_add_timeout_not_replayed(settings, namespace):
    downloader = Downloader(settings)
    client = downloader.client
    client._request = Mock(side_effect=requests.Timeout())
    with pytest.raises(requests.Timeout):
        client._request_manager(http_method="post", api_namespace=namespace, api_method="add")
    assert client._request.call_count == 1


def test_never_adopt_foreign_torrent(settings):
    d = Downloader(settings)
    d.client = Mock()
    d.client.torrents_info.return_value = [{"category": "radarr"}]
    with pytest.raises(ValueError, match="OWNED_BY_ANOTHER"):
        d.add(b"not even parsed", "a" * 40, "movie")
    d.client.torrents_add.assert_not_called()


def test_select_only_missing_episode_before_resuming(settings):
    d = Downloader(settings)
    client = d.client = Mock()
    info = {"category": "mae", "tags": "media-acquisition-engine,mae-selection-pending"}
    files = [
        {"index": 0, "name": "Example.S01E01.mkv", "priority": 1},
        {"index": 1, "name": "Example.S01E02.mkv", "priority": 1},
    ]
    client.torrents_info.return_value = [info]
    client.torrents_files.side_effect = lambda **_kwargs: files

    def set_priority(**kwargs):
        files[kwargs["file_ids"]]["priority"] = kwargs["priority"]

    client.torrents_file_priority.side_effect = set_priority
    d.complete_selection("a" * 40, ["S01E02"])

    assert [row["priority"] for row in files] == [0, 1]
    client.torrents_resume.assert_called_once()
    client.torrents_remove_tags.assert_called_once()


def test_selection_waits_for_qbittorrent_file_table(settings, monkeypatch):
    d = Downloader(settings)
    client = d.client = Mock()
    client.torrents_info.return_value = [{"category": "mae", "tags": "mae-selection-pending"}]
    file = {"index": 0, "name": "Example.S01E02.mkv", "priority": 1}
    client.torrents_files.side_effect = [[], [file], [file]]
    monkeypatch.setattr("mae.downloads.time.sleep", lambda _delay: None)

    d.complete_selection("a" * 40, ["S01E02"])

    assert client.torrents_files.call_count == 3
    client.torrents_resume.assert_called_once()


def test_selection_must_verify_priorities_before_resuming(settings):
    d = Downloader(settings)
    client = d.client = Mock()
    client.torrents_info.return_value = [{"category": "mae", "tags": "mae-selection-pending"}]
    client.torrents_files.return_value = [{"index": 0, "name": "Example.S01E01.mkv", "priority": 0}]
    with pytest.raises(Exception, match="TEMPORARY_FAILURE"):
        d.complete_selection("a" * 40, ["S01E01"])
    client.torrents_resume.assert_not_called()


def test_selection_rejects_combined_file_with_unrequested_episode(settings):
    d = Downloader(settings)
    client = d.client = Mock()
    client.torrents_info.return_value = [{"category": "mae", "tags": "mae-selection-pending"}]
    client.torrents_files.return_value = [
        {"index": 0, "name": "Example.S01E01-E02.mkv", "priority": 1}
    ]
    with pytest.raises(ValueError, match="TORRENT_FILE_COVERS_UNMONITORED_EPISODE"):
        d.complete_selection("a" * 40, ["S01E02"])
    client.torrents_file_priority.assert_not_called()
