"""Read-only public studio catalogs; deliberately separate from download providers."""

from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from mae.domain import Outcome, ProviderError
from mae.security import clean_text


@dataclass(frozen=True)
class CatalogEntry:
    studio: str
    title: str
    url: str
    synopsis: str


class StudioCatalog:
    max_entries = 250

    def __init__(self, studio, base_url, http):
        self.studio, self.base_url, self.http = studio, base_url, http

    def entries(self):
        raise NotImplementedError

    def capabilities(self):
        return {"catalog"}

    def authentication_status(self):
        return {"source": "anonymous", "mode": "catalog_only"}

    def healthcheck(self):
        return {"status": "OK", "capabilities": sorted(self.capabilities()), "entries": len(self.entries())}

    def search(self, query):
        raise ProviderError(Outcome.UNSUPPORTED_CAPABILITY)

    def soup(self, url):
        try:
            raw = self.http.get(url)
            text = raw.decode("utf-8", "strict")
        except UnicodeError:
            raise ProviderError(Outcome.PARSER_BROKEN) from None
        return BeautifulSoup(text, "html.parser")

    def entry_url(self, href):
        """Keep catalog links on the studio's HTTPS origin."""
        url = urljoin(self.base_url, href)
        parsed, base = urlsplit(url), urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != base.hostname
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
        ):
            return None
        return url

    def bounded(self, entries):
        unique, seen = [], set()
        for entry in entries:
            if entry.url not in seen:
                unique.append(entry)
                seen.add(entry.url)
            if len(unique) == self.max_entries:
                break
        return unique


class AlexFilmCatalog(StudioCatalog):
    def __init__(self, http):
        super().__init__("AlexFilm", "https://alexfilm.tv/", http)

    def entries(self):
        soup = self.soup(self.base_url)
        result = []
        for title in soup.select(".elementor-post__title a[href]"):
            text = clean_text(title.get_text(" ", strip=True))
            url = self.entry_url(title["href"])
            if text and url:
                excerpt = title.find_parent("article") or title.find_parent(
                    class_=lambda value: value and "elementor-post" in value
                )
                result.append(
                    CatalogEntry(
                        self.studio,
                        text,
                        url,
                        clean_text(str(excerpt.select_one(".elementor-post__excerpt")))
                        if excerpt and excerpt.select_one(".elementor-post__excerpt")
                        else "",
                    )
                )
        return self.bounded(result)


class FlarrowCatalog(StudioCatalog):
    def __init__(self, http):
        super().__init__("Flarrow Films", "https://fla-fi.studio/", http)

    def entries(self):
        soup = self.soup(self.base_url)
        result = []
        for card in soup.select(".short-desc"):
            parent = card.parent
            link = parent.select_one("a[href]") if parent else None
            title = clean_text(link.get_text(" ", strip=True)) if link else ""
            url = self.entry_url(link["href"]) if link else None
            if title and url:
                result.append(CatalogEntry(self.studio, title, url, clean_text(str(card))))
        return self.bounded(result)


class LostFilmCatalog(StudioCatalog):
    def __init__(self, http):
        super().__init__("LostFilm", "https://www.lostfilm.tv/", http)

    def entries(self):
        soup = self.soup(self.base_url)
        result = []
        for card in soup.select('a.text-block[href^="/series/"]'):
            title = (
                clean_text(card.select_one(".title-ru").get_text(" ", strip=True))
                if card.select_one(".title-ru")
                else ""
            )
            url = self.entry_url(card["href"])
            if title and url:
                result.append(
                    CatalogEntry(
                        self.studio,
                        title,
                        url,
                        clean_text(card.select_one(".title-en").get_text(" ", strip=True))
                        if card.select_one(".title-en")
                        else "",
                    )
                )
        return self.bounded(result)

    def search(self, query):
        soup = self.soup(self.base_url + "search/?q=" + quote(query, safe=""))
        result = []
        for link in soup.select('.row-search a.no-decoration[href^="/series/"]'):
            ru, en = link.select_one(".name-ru"), link.select_one(".name-en")
            title = clean_text(ru.get_text(" ", strip=True)) if ru else ""
            url = self.entry_url(link["href"])
            if title and url:
                result.append(
                    CatalogEntry(
                        self.studio, title, url, clean_text(en.get_text(" ", strip=True)) if en else ""
                    )
                )
        return self.bounded(result)


class RedHeadSoundCatalog(StudioCatalog):
    def __init__(self, http):
        super().__init__("Red Head Sound", "https://redheadsound.studio/", http)

    def entries(self):
        soup = self.soup(self.base_url)
        result = []
        for card in soup.select(".move-item"):
            link = card.select_one(".move-item__content h4 a[href]")
            if link:
                title = clean_text(link.get_text(" ", strip=True))
                url = self.entry_url(link["href"])
                if title and url:
                    result.append(CatalogEntry(self.studio, title, url, ""))
        return self.bounded(result)
