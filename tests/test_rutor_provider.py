from mae.config import ProviderConfig
from mae.domain import Work
from mae.providers import RutorProvider


class HTTP:
    def __init__(self):
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return b"""<div id="index"><table><tr class="gai"><td>date</td><td><a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567">M</a><a href="/torrent/42/example">Example 2024</a></td><td>667.05 MB</td><td>0</td></tr></table></div>"""


def test_rutor_public_search_returns_magnet_and_card_url():
    config = ProviderConfig(
        name="rutor",
        protocol="rutor",
        url="https://rutor.info",
        source_host="rutor.info",
        details_adapter="rutor",
    )
    result = RutorProvider(config, HTTP()).search(Work(kind="movie", external_id=1, title="Example"))[0]
    assert result.details_url == "https://rutor.info/torrent/42/example"
    assert result.size == 699452620
    assert RutorProvider(config, HTTP()).capabilities() == {"search", "details", "file_list", "download"}


def test_rutor_search_percent_encodes_title_in_path():
    config = ProviderConfig(
        name="rutor",
        protocol="rutor",
        url="https://rutor.info",
        source_host="rutor.info",
        details_adapter="rutor",
    )
    http = HTTP()

    RutorProvider(config, http).search(Work(kind="movie", external_id=1, title="Тест / #?"))

    assert http.urls == ["https://rutor.info/search/0/0/000/0/%D0%A2%D0%B5%D1%81%D1%82%20%2F%20%23%3F"]


def test_rutor_parses_localized_decimal_size():
    class LocalizedHTTP(HTTP):
        def get(self, url, **kwargs):
            return b"""<div id="index"><table><tr class="gai"><td>date</td><td><a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567">M</a><a href="/torrent/42/example">Example</a></td><td>1,5 \xd0\x93\xd0\x91</td><td>0</td></tr></table></div>"""

    config = ProviderConfig(
        name="rutor",
        protocol="rutor",
        url="https://rutor.info",
        source_host="rutor.info",
        details_adapter="rutor",
    )
    result = RutorProvider(config, LocalizedHTTP()).search(
        Work(kind="movie", external_id=1, title="Example")
    )[0]

    assert result.size == int(1.5 * 1024**3)
