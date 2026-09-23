import httpx
import pytest

from mae.config import ProviderConfig
from mae.domain import Work
from mae.providers import NNMClubProvider
from mae.security import SafeHTTP


class HTTP:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kwargs):
        return self.pages[url]


def test_topic_query_survives_real_http_client(respx_mock):
    config = ProviderConfig(name="nnm", protocol="nnmclub", url="https://nnmclub.to", interval=0)
    provider = NNMClubProvider(config, SafeHTTP("https://nnmclub.to"))
    respx_mock.get("https://nnmclub.to/forum/tracker.php", params={"nm": "Example"}).respond(
        200, text='<a></a><table><tr><td><a class="topictitle" href="viewtopic.php?t=42">Example</a>'
        '<a href="download.php?id=7">DL</a></td></tr></table>'
    )
    card = respx_mock.get("https://nnmclub.to/forum/viewtopic.php?t=42").mock(
        return_value=httpx.Response(200, content='<div class="post_body">Аудио: Русский</div>'.encode("cp1251"))
    )
    release = provider.search(Work(kind="movie", external_id=1, title="Example"))[0]
    assert "Русский" in provider.fetch_details(release).evidence[0].text
    assert card.called


@pytest.mark.parametrize("post_class", ["postbody", "post_body"])
def test_card_first_post_only(post_class):
    from mae.domain import SearchResult
    url = "https://nnmclub.to/forum/viewtopic.php?t=42"
    config = ProviderConfig(name="nnm", protocol="nnmclub", url="https://nnmclub.to")
    html = (f'<span class="{post_class}">Аудио: Английский</span>'
            f'<span class="{post_class}">Комментарий: Русский LostFilm</span>').encode("cp1251")
    provider = NNMClubProvider(config, HTTP({url: html}))
    details = provider.fetch_details(SearchResult(
        provider="nnm", release_id="42", title="Example", details_url=url
    ))
    assert details.evidence[0].text == "Аудио: Английский"
    assert details.evidence[0].method == "nnmclub-card-v2"


def test_malformed_breaks_preserve_audio_evidence():
    from mae.domain import Evidence, ReleaseDetails, SearchResult
    from mae.parsing import parse_candidate
    from mae.security import clean_text
    text = clean_text('<div>Описание<br>Сюжет<br><span>Аудио:</span> Русский</br></br></div>')
    assert "Аудио: Русский" in text
    candidate = parse_candidate(
        SearchResult(provider="nnm",release_id="42",title="Example.2024.1080p.WEB-DL.H264"),
        ReleaseDetails(evidence=[Evidence(id="card",text=text,method="fixture")]),
    )
    assert any(c.field == "audio_language" and c.value == "rus" for c in candidate.claims)


@pytest.mark.parametrize("label,expected", [
    ("Язык озвучки",True),("Язык аудио",True),("Язык субтитров",False),("Язык",False)
])
def test_explicit_audio_language_labels(label, expected):
    from mae.domain import Evidence, ReleaseDetails, SearchResult
    from mae.parsing import parse_candidate
    candidate = parse_candidate(
        SearchResult(provider="nnm",release_id="42",title="Example.2024.1080p.WEB-DL.H264"),
        ReleaseDetails(evidence=[Evidence(id="card",text=label+": Русский",method="fixture")]),
    )
    assert any(c.field == "audio_language" and c.value == "rus" for c in candidate.claims) is expected


@pytest.mark.parametrize("seed_text,expected", [("0",0),("27",27),("—",None)])
def test_nnm_exact_bytes_and_unknown_seeders(seed_text, expected):
    config = ProviderConfig(name="nnm",protocol="nnmclub",url="https://nnmclub.to")
    body = ('<table><tr><td><a class="topictitle" href="viewtopic.php?t=42">Example</a>'
            '<a href="download.php?id=7">DL</a></td>'
            '<td><u>5006225955</u> 4.66 GB</td>'
            f'<td class="seedmed">{seed_text}</td></tr></table>').encode('cp1251')
    p = NNMClubProvider(config, HTTP({'https://nnmclub.to/forum/tracker.php':body}))
    release = p.search(Work(kind="movie",external_id=1,title="Example"))[0]
    assert release.size == 5006225955 and release.seeders == expected


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


def test_nnmclub_undefined_cp1251_byte_does_not_hide_valid_results():
    from mae.domain import Outcome, ProviderError

    config = ProviderConfig(name="nnm", protocol="nnmclub", url="https://nnmclub.to")
    search = (
        b'<form id="search_form"></form>\x98<table><tr><td>'
        b'<a class="topictitle" href="viewtopic.php?t=42">Example.2026.1080p</a>'
        b'<a href="download.php?id=7">DL</a></td></tr></table>'
    )
    card = '<div class="post_body">Аудио: Русский</div>'.encode("windows-1251") + b"\x98"
    provider = NNMClubProvider(config, HTTP({
        "https://nnmclub.to/forum/tracker.php": search,
        "https://nnmclub.to/forum/viewtopic.php?t=42": card,
    }))
    result = provider.search(Work(kind="movie", external_id=1, title="Example"))
    assert len(result) == 1
    assert "Русский" in provider.fetch_details(result[0]).evidence[0].text
    with pytest.raises(ProviderError) as error:
        provider._decode(search + b"\x98" * 33)
    assert error.value.outcome == Outcome.PARSER_BROKEN


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
