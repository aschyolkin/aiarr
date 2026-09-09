from mae.studio_catalogs import AlexFilmCatalog, FlarrowCatalog, LostFilmCatalog


class HTTP:
    def get(self, url):
        return b'<article class="elementor-post"><h3 class="elementor-post__title"><a href="https://alexfilm.tv/serialy/x/">Example</a></h3><div class="elementor-post__excerpt"><p>Synopsis</p></div></article>'


def test_alexfilm_catalog_is_read_only_metadata():
    entry = AlexFilmCatalog(HTTP()).entries()[0]
    assert (entry.studio, entry.title, entry.url, entry.synopsis) == ("AlexFilm", "Example", "https://alexfilm.tv/serialy/x/", "Synopsis")


def test_flarrow_catalog_is_read_only_metadata():
    class FlarrowHTTP:
        def get(self, url):
            return b'<div><a href="https://fla-fi.studio/serial/x/">Example</a><div class="short-desc"><div class="sd-line">Dubbed</div></div></div>'

    entry = FlarrowCatalog(FlarrowHTTP()).entries()[0]
    assert (entry.studio, entry.title, entry.url, entry.synopsis) == ("Flarrow Films", "Example", "https://fla-fi.studio/serial/x/", "Dubbed")


def test_lostfilm_catalog_is_read_only_metadata():
    class LostHTTP:
        def get(self, url):
            return b'<a class="text-block" href="/series/Example"><div class="title-ru">Example RU</div><div class="title-en">Example EN</div></a>'

    entry = LostFilmCatalog(LostHTTP()).entries()[0]
    assert (entry.studio, entry.title, entry.url, entry.synopsis) == ("LostFilm", "Example RU", "https://www.lostfilm.tv/series/Example", "Example EN")
