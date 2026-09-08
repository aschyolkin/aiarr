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
