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

    assert LostFilmCatalog(LostHTTP()).search("Example")[0].url == "https://www.lostfilm.tv/series/Example"


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
                <a href="/serialy/safe/">Safe</a></h3></article>"""

    assert [(entry.title, entry.url) for entry in AlexFilmCatalog(UnsafeHTTP()).entries()] == [
        ("Safe", "https://alexfilm.tv/serialy/safe/")
    ]
