import socket

import httpx
import pytest

from mae.config import ProviderConfig
from mae.domain import Outcome, ProviderError
from mae.providers import JSONProvider, TorznabProvider
from mae.security import SafeHTTP


def provider():
    return TorznabProvider(
        ProviderConfig(name="test", url="http://gateway.test/api", trusted_gateway=True, interval=0)
    )


def test_torznab_real_parser(respx_mock, movie):
    xml = """<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item>
    <title>Example.2024.1080p.WEB-DL.H264</title><guid>private-guid</guid>
    <description>Audio: Russian</description><enclosure url="http://gateway.test/file" length="1000"/>
    <torznab:attr name="seeders" value="5"/></item></channel></rss>"""
    respx_mock.get("http://gateway.test/api").mock(return_value=httpx.Response(200, text=xml))
    p = provider()
    result = p.search(movie)[0]
    assert result.seeders == 5 and result.size == 1000
    assert result.release_id != "private-guid"
    assert p.fetch_details(result).evidence[0].text == "Audio: Russian"
    assert p.resolve_download(result).url.get_secret_value() == "http://gateway.test/file"


@pytest.mark.parametrize(
    "body,outcome",
    [
        ('<html><input type="password"></html>', Outcome.AUTH_REQUIRED),
        ("<rss><broken>", Outcome.PARSER_BROKEN),
        ("<rss/>", Outcome.PARSER_BROKEN),
        ('<error code="100"/>', Outcome.AUTH_REQUIRED),
        ('<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss>&x;</rss>', Outcome.PARSER_BROKEN),
    ],
)
def test_error_not_empty(body, outcome, respx_mock, movie):
    respx_mock.get("http://gateway.test/api").mock(return_value=httpx.Response(200, text=body))
    with pytest.raises(ProviderError) as exc:
        provider().search(movie)
    assert exc.value.outcome == outcome


def test_no_results(respx_mock, movie):
    respx_mock.get("http://gateway.test/api").mock(
        return_value=httpx.Response(200, text="<rss><channel/></rss>")
    )
    assert provider().search(movie) == []


def test_retry_after(respx_mock):
    respx_mock.get("http://gateway.test/api").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "123"})
    )
    with pytest.raises(ProviderError) as exc:
        provider().healthcheck()
    assert exc.value.outcome == Outcome.RATE_LIMITED and exc.value.retry_after == 123


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/foo",
        "https://127.0.0.1/foo",
        "file:///etc/passwd",
        "https://user:password@example.com/foo",
        "https://169.254.169.254/latest",
    ],
)
def test_ssrf(url):
    with pytest.raises(ProviderError):
        SafeHTTP().get(url)


def test_redirect_not_followed(respx_mock):
    respx_mock.get("http://gateway.test/api").mock(
        return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
    )
    with pytest.raises(ProviderError):
        provider().healthcheck()
    assert len(respx_mock.calls) == 1


@pytest.mark.parametrize("prefix", ["//", "https://"])
def test_nnm_bulk_redirect_is_one_exact_https_hop(respx_mock, monkeypatch, prefix):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
    ])
    start = "https://nnmclub.to/forum/download.php?id=7"
    bulk = prefix + "bulk.nnmclub.to/download.php?csid=private&uid=1&id=7"
    respx_mock.get(start).mock(return_value=httpx.Response(302, headers={"Location": bulk}))
    respx_mock.get("https://93.184.216.34/download.php").mock(return_value=httpx.Response(200, content=b"torrent"))
    http = SafeHTTP("https://nnmclub.to")
    assert http.get(start, allowed_redirect=("bulk.nnmclub.to", "/download.php")) == b"torrent"
    assert len(respx_mock.calls) == 2


@pytest.mark.parametrize("location", [
    "http://bulk.nnmclub.to/download.php?id=7",
    "https://bulk.nnmclub.to/other.php?id=7",
    "https://elsewhere.example/download.php?id=7",
    "https://bulk.nnmclub.to:444/download.php?id=7",
])
def test_nnm_bulk_redirect_rejects_other_targets(respx_mock, location):
    start = "https://nnmclub.to/forum/download.php?id=7"
    respx_mock.get(start).mock(return_value=httpx.Response(302, headers={"Location": location}))
    with pytest.raises(ProviderError) as error:
        SafeHTTP("https://nnmclub.to").get(start, allowed_redirect=("bulk.nnmclub.to", "/download.php"))
    assert error.value.outcome == Outcome.ACCESS_DENIED
    assert len(respx_mock.calls) == 1


def test_new_protocol_same_contract(respx_mock, movie):
    config = ProviderConfig(
        name="owned-json", protocol="json", url="http://json.test/api", trusted_gateway=True, interval=0
    )
    respx_mock.get(config.url).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "release": {
                            "release_id": "42",
                            "title": "Example.2024.1080p.WEB-DL.H264",
                            "size": 1000,
                        },
                        "description": "Audio: Russian",
                        "download": "http://json.test/file",
                    }
                ]
            },
        )
    )
    p = JSONProvider(config)
    r = p.search(movie)[0]
    assert p.fetch_details(r).evidence and p.resolve_download(r)
