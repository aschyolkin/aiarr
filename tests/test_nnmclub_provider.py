from mae.config import ProviderConfig
from mae.domain import Work
from mae.providers import NNMClubProvider


class HTTP:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kwargs):
        return self.pages[url]


def test_nnmclub_anonymous_search_card_and_handle():
    config = ProviderConfig(name="nnm", protocol="nnmclub", url="https://nnmclub.to", encoding="windows-1251")
    search = b'''<form id="search_form"></form><table><tr><td><a class="topictitle" href="viewtopic.php?t=42"><b>Example / Test</b></a></td><td><a href="download.php?id=7">DL</a></td></tr></table>'''
    card = "<div class='post_body'><b>Аудио:</b> Русский</div>".encode("windows-1251")
    provider = NNMClubProvider(config, HTTP({"https://nnmclub.to/forum/tracker.php": search, "https://nnmclub.to/forum/viewtopic.php?t=42": card}))
    result = provider.search(Work(kind="movie", external_id=1, title="Example"))[0]
    assert result.details_url == "https://nnmclub.to/forum/viewtopic.php?t=42"
    assert provider.resolve_download(result).url.get_secret_value() == "https://nnmclub.to/forum/download.php?id=7"
    assert "Русский" in provider.fetch_details(result).evidence[0].text
