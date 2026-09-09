import pytest

from mae.domain import Outcome, ProviderError
from mae.studio_catalogs import AlexFilmCatalog, FlarrowCatalog, LostFilmCatalog, RedHeadSoundCatalog


class HTTP:
    def get(self, url):
        return b'<article class="elementor-post"><h3 class="elementor-post__title"><a href="https://alexfilm.tv/serialy/x/">Example</a></h3><div class="elementor-post__excerpt"><p>Synopsis</p></div></article>'


def test_alexfilm_catalog_is_read_only_metadata():
    catalog = AlexFilmCatalog(HTTP())
    entry = catalog.entries()[0]
    assert (entry.studio, entry.title, entry.url, entry.synopsis) == (
        "AlexFilm",
        "Example",
        "https://alexfilm.tv/serialy/x/",
        "Synopsis",
    )
    assert catalog.healthcheck() == {"status": "OK", "capabilities": ["catalog"], "entries": 1}
    assert catalog.authentication_status() == {"source": "anonymous", "mode": "catalog_only"}
    assert not hasattr(catalog, "resolve_download")
    with pytest.raises(ProviderError) as exc:
        catalog.search("Example")
    assert exc.value.outcome == Outcome.UNSUPPORTED_CAPABILITY


def test_flarrow_catalog_is_read_only_metadata():
    class FlarrowHTTP:
        def get(self, url):
            return b'<div><a href="https://fla-fi.studio/serial/x/">Example</a><div class="short-desc"><div class="sd-line">Dubbed</div></div></div>'

    entry = FlarrowCatalog(FlarrowHTTP()).entries()[0]
    assert (entry.studio, entry.title, entry.url, entry.synopsis) == (
        "Flarrow Films",
        "Example",
        "https://fla-fi.studio/serial/x/",
        "Dubbed",
    )


def test_lostfilm_catalog_is_read_only_metadata():
    class LostHTTP:
        def get(self, url):
            return b'<a class="text-block" href="/series/Example"><div class="title-ru">Example RU</div><div class="title-en">Example EN</div></a>'

    entry = LostFilmCatalog(LostHTTP()).entries()[0]
    assert (entry.studio, entry.title, entry.url, entry.synopsis) == (
        "LostFilm",
        "Example RU",
        "https://www.lostfilm.tv/series/Example",
        "Example EN",
    )


def test_lostfilm_catalog_search_excludes_movies():
    class LostHTTP:
        def get(self, url):
            return b'<div class="row-search"><a class="no-decoration" href="/series/Example"><div class="name-ru">Example RU</div><div class="name-en">Example EN</div></a></div><div class="row-search"><a class="no-decoration" href="/movies/Other"><div class="name-ru">Film</div></a></div>'

    catalog = LostFilmCatalog(LostHTTP())
    assert catalog.search("Example")[0].url == "https://www.lostfilm.tv/series/Example"


def test_lostfilm_catalog_search_encodes_query():
    class LostHTTP:
        def __init__(self):
            self.urls = []

        def get(self, url):
            self.urls.append(url)
            return b""

    http = LostHTTP()
    assert LostFilmCatalog(http).search("\u0422\u0435\u0441\u0442 / #?") == []
    assert http.urls == ["https://www.lostfilm.tv/search/?q=%D0%A2%D0%B5%D1%81%D1%82%20%2F%20%23%3F"]


def test_red_head_sound_catalog_is_read_only_metadata():
    class RHSHTTP:
        def get(self, url):
            return b'<div class="move-item"><div class="move-item__content"><h4><a href="https://redheadsound.studio/video/example">Example</a></h4></div></div>'

    entry = RedHeadSoundCatalog(RHSHTTP()).entries()[0]
    assert (entry.studio, entry.title, entry.url) == (
        "Red Head Sound",
        "Example",
        "https://redheadsound.studio/video/example",
    )


def test_catalog_rejects_external_and_non_https_links():
    class UnsafeHTTP:
        def get(self, url):
            return b"""<article class="elementor-post"><h3 class="elementor-post__title">
                <a href="javascript:alert(1)">Script</a></h3></article>
                <article class="elementor-post"><h3 class="elementor-post__title">
                <a href="https://other.example/release">External</a></h3></article>
                <article class="elementor-post"><h3 class="elementor-post__title">
                <a href="https://alexfilm.tv:444/alternate">Alternate port</a></h3></article>
                <article class="elementor-post"><h3 class="elementor-post__title">
                <a href="https://user@example.invalid@alexfilm.tv/credentials">Userinfo</a></h3></article>
                <article class="elementor-post"><h3 class="elementor-post__title">
                <a href="/serialy/safe/">Safe</a></h3></article>"""

    assert [(entry.title, entry.url) for entry in AlexFilmCatalog(UnsafeHTTP()).entries()] == [
        ("Safe", "https://alexfilm.tv/serialy/safe/")
    ]


def test_catalog_invalid_utf8_is_explicit_parser_error():
    class BrokenHTTP:
        def get(self, url):
            return b"\xff"

    with pytest.raises(ProviderError) as exc:
        AlexFilmCatalog(BrokenHTTP()).entries()
    assert exc.value.outcome == Outcome.PARSER_BROKEN


def test_catalog_deduplicates_and_bounds_public_cards():
    class RepeatedHTTP:
        def get(self, url):
            card = '<article class="elementor-post"><h3 class="elementor-post__title"><a href="/serialy/{0}/">{0}</a></h3></article>'
            return (card.format("same") * 2 + "".join(card.format(i) for i in range(300))).encode()

    entries = AlexFilmCatalog(RepeatedHTTP()).entries()
    assert len(entries) == 250
    assert entries[0].title == "same"
    assert len({entry.url for entry in entries}) == 250
