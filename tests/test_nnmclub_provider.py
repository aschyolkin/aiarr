import pytest

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
    search = b"""<form id="search_form"></form><table><tr><td><a class="topictitle" href="viewtopic.php?t=42"><b>Example / Test</b></a></td><td><a href="download.php?id=7">DL</a></td></tr></table>"""
    card = "<div class='post_body'><b>Аудио:</b> Русский</div>".encode("windows-1251")
    provider = NNMClubProvider(
        config,
        HTTP(
            {
                "https://nnmclub.to/forum/tracker.php": search,
                "https://nnmclub.to/forum/viewtopic.php?t=42": card,
            }
        ),
    )
    result = provider.search(Work(kind="movie", external_id=1, title="Example"))[0]
    assert result.details_url == "https://nnmclub.to/forum/viewtopic.php?t=42"
    assert (
        provider.resolve_download(result).url.get_secret_value()
        == "https://nnmclub.to/forum/download.php?id=7"
    )
    assert "Русский" in provider.fetch_details(result).evidence[0].text


@pytest.mark.parametrize(
    "config",
    [
        {"protocol": "nnmclub", "url": "http://nnmclub.to"},
        {"protocol": "nnmclub", "url": "https://mirror.example"},
        {
            "protocol": "nnmclub",
            "url": "https://nnmclub.to",
            "source_host": "nnmclub.to",
            "details_selector": "p",
        },
        {
            "protocol": "rutor",
            "url": "http://rutor.info",
            "source_host": "rutor.info",
            "details_adapter": "rutor",
        },
        {
            "protocol": "rutor",
            "url": "https://rutor.info",
            "source_host": "other.example",
            "details_adapter": "rutor",
        },
        {
            "protocol": "rutor",
            "url": "https://mirror.example",
            "source_host": "mirror.example",
            "details_adapter": "rutor",
        },
        {
            "protocol": "rutor",
            "url": "https://rutor.info",
            "source_host": "rutor.info",
        },
        {
            "protocol": "rutor",
            "url": "https://rutor.info/?mirror=1",
            "source_host": "rutor.info",
            "details_adapter": "rutor",
        },
        {"protocol": "nnmclub", "url": "https://nnmclub.to/forum/"},
        {"protocol": "nnmclub", "url": "https://nnmclub.to/#fragment"},
    ],
)
def test_special_provider_rejects_unverified_mirrors_and_mismatched_origins(config):
    with pytest.raises(ValueError):
        ProviderConfig(name="special", **config)
