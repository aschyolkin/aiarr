from mae.config import ProviderConfig
from mae.domain import Work
from mae.providers import RutorProvider


class HTTP:
    def get(self, url, **kwargs):
        return b'''<div id="index"><table><tr class="gai"><td>date</td><td><a href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567">M</a><a href="/torrent/42/example">Example 2024</a></td><td>667.05 MB</td><td>0</td></tr></table></div>'''


def test_rutor_public_search_returns_magnet_and_card_url():
    config = ProviderConfig(name="rutor", protocol="rutor", url="https://rutor.info", source_host="rutor.info", details_adapter="rutor")
    result = RutorProvider(config, HTTP()).search(Work(kind="movie", external_id=1, title="Example"))[0]
    assert result.details_url == "https://rutor.info/torrent/42/example"
    assert result.size == 699452620
    assert RutorProvider(config, HTTP()).capabilities() == {"search", "details", "file_list", "download"}
