from unittest.mock import Mock

import httpx
import pytest

from mae.config import ProviderConfig
from mae.domain import Outcome, ProviderError, ReleaseDetails, SearchResult
from mae.enrichment import HTMLDetailsEnricher
from mae.parsing import parse_candidate
from mae.security import clean_text


@pytest.mark.parametrize(
    "markup,rus",
    [
        ("<div><b>Аудио:</b> русский, AAC <i>LostFilm</i></div>", True),
        ("[b]Audio:[/b] Russian, AAC", True),
        ("<pre>Audio\nLanguage : Russian\nText\nLanguage : English</pre>", True),
        ("<b>Audio:</b> English<br><b>Subtitles:</b> Russian", False),
        ("<span>Audio:</span><span>English</span><span>Subtitles:</span><span>Russian</span>", False),
        ("[b]Audio:[/b] English\n[b]Subtitles:[/b] Russian", False),
        ("<div>Video</div><div>Language: Russian</div>", False),
    ],
)
def test_inline_markup_preserves_audio_scope(markup, rus):
    from mae.domain import Evidence

    release = SearchResult(provider="fixture", release_id="1", title="Example.2024.1080p.WEB-DL.H264")
    details = ReleaseDetails(evidence=[Evidence(id="card", text=clean_text(markup), method="fixture")])
    candidate = parse_candidate(release, details)
    assert any(c.field == "audio_language" and c.value == "rus" for c in candidate.claims) is rus


def make_enricher(body, encoding="utf-8"):
    config = ProviderConfig(
        name="fixture",
        url="https://gateway.example/api",
        source_host="source.example",
        details_selector="#release",
        encoding=encoding,
        interval=0,
        cookie="private-cookie",
    )
    http = Mock()
    http.get.return_value = body
    release = SearchResult(
        provider="fixture", release_id="1", title="Example", details_url="https://source.example/topic/1"
    )
    return HTMLDetailsEnricher(config, http), http, release


def test_separate_card_cookie_encoding_cache_and_redaction():
    body = '<form><input type="password"></form><div id="release"><b>Аудио:</b> русский<br>pass<b>key</b>=secret<script>bad()</script></div>'
    enricher, http, release = make_enricher(body.encode("cp1251"), "windows-1251")
    first = enricher.fetch_details(release)
    assert "Аудио: русский" in first.evidence[0].text
    assert "secret" not in first.evidence[0].text and "bad()" not in first.evidence[0].text
    first.evidence.clear()
    assert enricher.fetch_details(release).evidence
    http.get.assert_called_once_with(release.details_url, headers={"Cookie": "private-cookie"})
    wrong_host = release.model_copy(update={"details_url": "https://elsewhere.example/topic/1"})
    with pytest.raises(ProviderError) as error:
        enricher.fetch_details(wrong_host)
    assert error.value.outcome == Outcome.ACCESS_DENIED
    assert http.get.call_count == 1


@pytest.mark.parametrize(
    "body,outcome",
    [
        (b'<form><input type="password"></form>', Outcome.AUTH_REQUIRED),
        (b'<div id="release"><input type="password"></div>', Outcome.AUTH_REQUIRED),
        (b'<div id="changed">Audio: Russian</div>', Outcome.PARSER_BROKEN),
        (b'<div id="release"><script>hidden</script></div>', Outcome.PARSER_BROKEN),
        (b'<div id="release">\xff</div>', Outcome.PARSER_BROKEN),
    ],
)
def test_auth_changed_layout_and_bad_encoding_never_become_empty(body, outcome):
    enricher, _, release = make_enricher(body)
    with pytest.raises(ProviderError) as error:
        enricher.fetch_details(release)
    assert error.value.outcome == outcome


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_host": "example.org"},
        {"details_selector": "#body"},
        {"encoding": "not-a-codec"},
        {"source_host": "example.org", "details_selector": "["},
    ],
)
def test_invalid_source_configuration_fails_at_startup(kwargs):
    with pytest.raises(ValueError):
        ProviderConfig(name="fixture", url="https://gateway.example/api", **kwargs)


def test_trusted_source_is_explicit_and_exact_origin(respx_mock):
    config = ProviderConfig(
        name="fixture",
        url="https://gateway.example/api",
        source_host="source.example",
        details_selector="#release",
        trusted_source=True,
    )
    respx_mock.get("https://source.example/topic/1").mock(
        return_value=httpx.Response(200, text='<div id="release">Audio: Russian</div>')
    )
    release = SearchResult(
        provider="fixture", release_id="1", title="Example", details_url="https://source.example/topic/1"
    )
    assert HTMLDetailsEnricher(config).fetch_details(release).evidence
    wrong_scheme = release.model_copy(update={"details_url": "http://source.example/topic/1"})
    with pytest.raises(ProviderError) as error:
        HTMLDetailsEnricher(config).fetch_details(wrong_scheme)
    assert error.value.outcome == Outcome.ACCESS_DENIED


def test_card_image_picks_first_poster_and_skips_decorations():
    from bs4 import BeautifulSoup

    from mae.enrichment import card_image

    html = (
        '<div><img src="/images/smiles/1.gif"><img src="https://x.test/icon.png" width="16">'
        '<var class="postImg" title="https://img.test/poster.jpg"></var>'
        '<img src="https://img.test/second.jpg"></div>'
    )
    assert card_image(BeautifulSoup(html, "html.parser"), "https://nnm.test/t?1") == "https://img.test/poster.jpg"
    assert card_image(BeautifulSoup('<img src="http://plain.test/a.jpg">', "html.parser"), "https://a.test/") is None
    assert card_image(BeautifulSoup('<img src="javascript:alert(1)">', "html.parser"), "https://a.test/") is None
