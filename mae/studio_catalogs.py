"""Read-only public studio catalogs; deliberately separate from download providers."""
from dataclasses import dataclass

from bs4 import BeautifulSoup

from mae.security import clean_text


@dataclass(frozen=True)
class CatalogEntry:
    studio: str
    title: str
    url: str
    synopsis: str


class StudioCatalog:
    def __init__(self, studio, base_url, http):
        self.studio, self.base_url, self.http = studio, base_url, http

    def entries(self):
        raise NotImplementedError


class AlexFilmCatalog(StudioCatalog):
    def __init__(self, http):
        super().__init__("AlexFilm", "https://alexfilm.tv/", http)

    def entries(self):
        soup = BeautifulSoup(self.http.get(self.base_url).decode("utf-8", "strict"), "html.parser")
        result = []
        for title in soup.select(".elementor-post__title a[href]"):
            text = clean_text(title.get_text(" ", strip=True))
            if text:
                excerpt = title.find_parent("article") or title.find_parent(
                    class_=lambda value: value and "elementor-post" in value
                )
                result.append(CatalogEntry(self.studio, text, title["href"], clean_text(str(excerpt.select_one(".elementor-post__excerpt"))) if excerpt and excerpt.select_one(".elementor-post__excerpt") else ""))
        return result


class FlarrowCatalog(StudioCatalog):
    def __init__(self, http):
        super().__init__("Flarrow Films", "https://fla-fi.studio/", http)

    def entries(self):
        soup = BeautifulSoup(self.http.get(self.base_url).decode("utf-8", "strict"), "html.parser")
        result = []
        for card in soup.select(".short-desc"):
            parent = card.parent
            link = parent.select_one("a[href]") if parent else None
            title = clean_text(link.get_text(" ", strip=True)) if link else ""
            if title:
                result.append(CatalogEntry(self.studio, title, link["href"], clean_text(str(card))))
        return result
