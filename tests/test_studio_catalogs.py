from mae.studio_catalogs import AlexFilmCatalog


class HTTP:
    def get(self, url):
        return b'<article class="elementor-post"><h3 class="elementor-post__title"><a href="https://alexfilm.tv/serialy/x/">Example</a></h3><div class="elementor-post__excerpt"><p>Synopsis</p></div></article>'


def test_alexfilm_catalog_is_read_only_metadata():
    entry = AlexFilmCatalog(HTTP()).entries()[0]
    assert (entry.studio, entry.title, entry.url, entry.synopsis) == ("AlexFilm", "Example", "https://alexfilm.tv/serialy/x/", "Synopsis")
