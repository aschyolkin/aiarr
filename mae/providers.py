import hashlib
import json
import re
import time
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
from defusedxml import ElementTree

from mae.config import ProviderConfig
from mae.domain import DownloadHandle, Evidence, Outcome, ProviderError, ReleaseDetails, SearchResult
from mae.enrichment import HTMLDetailsEnricher, RutorDetailsEnricher
from mae.security import SafeHTTP, clean_text


class TorznabProvider:
    def __init__(self, config: ProviderConfig, http=None):
        self.config = config
        self.http = http or SafeHTTP(config.url if config.trusted_gateway else None)
        self.handles = {}
        self.descriptions = {}
        self.last_request = 0.0
        adapter = RutorDetailsEnricher if config.details_adapter == "rutor" else HTMLDetailsEnricher
        self.enricher = adapter(config) if config.details_selector else None

    def capabilities(self):
        return {"search", "details", "download"} | (
            {"file_list"} if isinstance(self.enricher, RutorDetailsEnricher) else set()
        )

    def _get(self, **params):
        remaining = self.config.interval - (time.monotonic() - self.last_request)
        if remaining > 0:
            time.sleep(remaining)
        self.last_request = time.monotonic()
        return self.http.get(
            self.config.url, params={"apikey": self.config.api_key.get_secret_value(), **params}
        )

    def healthcheck(self):
        root = self._xml(self._get(t="caps"))
        if root.tag != "caps":
            raise ProviderError(Outcome.PARSER_BROKEN)
        return {"status": "OK", "capabilities": sorted(self.capabilities())}

    def authentication_status(self):
        # Gateway auth check is not a claim that the source account works.
        return {"gateway": self.healthcheck()["status"], "source": "unknown"}

    @staticmethod
    def _xml(raw):
        if b"<html" in raw[:1000].lower() or b'type="password"' in raw.lower():
            raise ProviderError(Outcome.AUTH_REQUIRED)
        try:
            root = ElementTree.fromstring(raw)
        except Exception:
            raise ProviderError(Outcome.PARSER_BROKEN) from None
        if root.tag == "error":
            code = root.get("code", "")
            raise ProviderError(
                Outcome.AUTH_REQUIRED
                if code in {"100", "101", "102"}
                else Outcome.RATE_LIMITED
                if code == "500"
                else Outcome.TEMPORARY_FAILURE
            )
        return root

    def search(self, query):
        # Generic search is universally supported; no invented ID capabilities.
        suffix = f" {query.year}" if query.kind == "movie" and query.year else ""
        root = self._xml(self._get(t="search", q=query.title + suffix, limit=100))
        if root.tag != "rss" or root.find("channel") is None:
            raise ProviderError(Outcome.PARSER_BROKEN)
        results = []
        for item in root.findall("./channel/item"):
            title = item.findtext("title")
            guid = item.findtext("guid")
            enclosure = item.find("enclosure")
            url = enclosure.get("url") if enclosure is not None else item.findtext("link")
            if not title or not guid or not url:
                raise ProviderError(Outcome.PARSER_BROKEN)
            # GUID can be a private URL; persist an opaque digest instead.
            rid = hashlib.sha256(guid.encode()).hexdigest()
            attrs = {a.get("name"): a.get("value") for a in item if a.tag.endswith("}attr")}
            raw_size = attrs.get("size") or (enclosure.get("length") if enclosure is not None else None)
            raw_peers = attrs.get("seeders")
            size = int(raw_size) if raw_size and raw_size.isdigit() else None
            peers = int(raw_peers) if raw_peers and raw_peers.isdigit() else None
            self.handles[rid] = DownloadHandle(url=urljoin(self.config.url, url))
            self.descriptions[rid] = clean_text(item.findtext("description") or "")
            details = item.findtext("comments")
            # Reject query-bearing detail URLs containing credential field names.
            if details and any(s in details.lower() for s in ("passkey", "apikey", "token", "password")):
                details = None
            results.append(
                SearchResult(
                    provider=self.config.name,
                    release_id=rid,
                    title=title,
                    size=size,
                    seeders=peers,
                    details_url=details,
                    published_at=item.findtext("pubDate"),
                )
            )
        return results

    def fetch_details(self, release):
        evidence = []
        if self.descriptions.get(release.release_id):
            evidence.append(
                Evidence(id="description", text=self.descriptions[release.release_id], method="torznab")
            )
        if self.enricher:
            evidence.extend(self.enricher.fetch_details(release).evidence)
        return ReleaseDetails(evidence=evidence)

    def fetch_file_list(self, release):
        if isinstance(self.enricher, RutorDetailsEnricher):
            return self.enricher.fetch_file_list(release)
        raise ProviderError(Outcome.UNSUPPORTED_CAPABILITY)

    def resolve_download(self, release):
        if release.release_id not in self.handles:
            raise ProviderError(Outcome.TEMPORARY_FAILURE)
        return self.handles[release.release_id]


class JSONProvider(TorznabProvider):
    """Documented JSON protocol for user-owned sources, not an arbitrary site API."""

    def healthcheck(self):
        raw = json.loads(self._get(operation="health"))
        if raw.get("status") != "OK":
            raise ProviderError(Outcome.TEMPORARY_FAILURE)
        return raw

    def search(self, query):
        try:
            data = json.loads(self._get(operation="search", q=query.title))
            rows = data["results"]
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError()
            results = []
            for row in rows:
                result = SearchResult(provider=self.config.name, **row["release"])
                self.handles[result.release_id] = DownloadHandle(url=row["download"])
                self.descriptions[result.release_id] = clean_text(row.get("description", ""))
                results.append(result)
            return results
        except (ValueError, KeyError, TypeError):
            raise ProviderError(Outcome.PARSER_BROKEN) from None


class NNMClubProvider:
    """Anonymous NNM-Club public search/card adapter (Windows-1251)."""

    def __init__(self, config, http=None):
        self.config = config
        self.http = http or SafeHTTP(f"https://{config.source_host}" if config.trusted_source else None)
        self.handles = {}
        self.last_request = 0.0

    def capabilities(self):
        return {"search", "details", "download"}

    def authentication_status(self):
        return {"source": "anonymous"}

    def healthcheck(self):
        self.search(type("Probe", (), {"title": "Big Buck Bunny", "year": None, "kind": "movie"})())
        return {"status": "OK", "capabilities": sorted(self.capabilities())}

    def _get(self, path, **params):
        delay = self.config.interval - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        # httpx treats params={} as replacing the URL query, dropping topic IDs.
        return self.http.get(urljoin(self.config.url, path), params=params or None)

    def search(self, query):
        raw = self._get("/forum/tracker.php", nm=query.title)
        try:
            soup = BeautifulSoup(raw.decode("windows-1251", errors="strict"), "html.parser")
        except UnicodeError:
            raise ProviderError(Outcome.PARSER_BROKEN) from None
        if soup.select_one('input[type="password"]'):
            raise ProviderError(Outcome.AUTH_REQUIRED)
        results = []
        for link in soup.select('a.topictitle[href^="viewtopic.php?t="]')[:100]:
            row = link.find_parent("tr")
            download = row.select_one('a[href^="download.php?id="]') if row else None
            title = clean_text(link.get_text(" ", strip=True))
            if not row or not download or not title:
                continue
            details_url = urljoin(self.config.url, "/forum/" + link["href"])
            rid = hashlib.sha256(details_url.encode()).hexdigest()
            self.handles[rid] = DownloadHandle(url=urljoin(self.config.url, "/forum/" + download["href"]))
            results.append(
                SearchResult(provider=self.config.name, release_id=rid, title=title, details_url=details_url)
            )
        if not results and soup.select_one("#search_form") is None:
            raise ProviderError(Outcome.PARSER_BROKEN)
        return results

    def fetch_details(self, release):
        if not release.details_url or not release.details_url.startswith(
            "https://nnmclub.to/forum/viewtopic.php?"
        ):
            raise ProviderError(Outcome.ACCESS_DENIED)
        raw = self._get(release.details_url)
        try:
            soup = BeautifulSoup(raw.decode("windows-1251", errors="strict"), "html.parser")
        except UnicodeError:
            raise ProviderError(Outcome.PARSER_BROKEN) from None
        # NNM's current phpBB layout uses postbody. Only the first post is
        # release evidence; replies must not contribute technical claims.
        card = soup.select_one(".post_body, .postbody")
        if card is None:
            if soup.select_one('input[type="password"]'):
                raise ProviderError(Outcome.AUTH_REQUIRED)
            raise ProviderError(Outcome.PARSER_BROKEN)
        text = clean_text(str(card))
        if not text:
            raise ProviderError(Outcome.PARSER_BROKEN)
        return ReleaseDetails(evidence=[Evidence(id="card", text=text, method="nnmclub-card-v2")])

    def fetch_file_list(self, release):
        raise ProviderError(Outcome.UNSUPPORTED_CAPABILITY)

    def resolve_download(self, release):
        try:
            return self.handles[release.release_id]
        except KeyError:
            raise ProviderError(Outcome.TEMPORARY_FAILURE) from None


class RutorProvider(NNMClubProvider):
    """Anonymous Rutor search; cards/file lists use the verified Rutor enricher."""

    def __init__(self, config, http=None):
        super().__init__(config, http)
        self.enricher = RutorDetailsEnricher(config, self.http)

    def capabilities(self):
        return {"search", "details", "file_list", "download"}

    def search(self, query):
        raw = self._get(f"/search/0/0/000/0/{quote(query.title, safe='')}")
        try:
            soup = BeautifulSoup(raw.decode("utf-8", errors="strict"), "html.parser")
        except UnicodeError:
            raise ProviderError(Outcome.PARSER_BROKEN) from None
        rows = []
        for row in soup.select("#index tr.gai, #index tr.tum")[:100]:
            detail = row.select_one('a[href^="/torrent/"]')
            magnet = row.select_one('a[href^="magnet:"]')
            cells = row.find_all("td", recursive=False)
            if not detail or not magnet or len(cells) < 4:
                continue
            title = clean_text(detail.get_text(" ", strip=True))
            if not title:
                continue
            url = urljoin(self.config.url, detail["href"])
            rid = hashlib.sha256(url.encode()).hexdigest()
            size_text = (
                cells[-2]
                .get_text(" ", strip=True)
                .replace(" ", "")
                .replace(",", ".")
                .translate(str.maketrans({"К": "K", "М": "M", "Г": "G", "Т": "T", "Б": "B"}))
            )
            match = re.fullmatch(r"([0-9.]+)(KB|MB|GB|TB)", size_text, re.I)
            multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
            size = int(float(match[1]) * multipliers[match[2].upper()]) if match else None
            self.handles[rid] = DownloadHandle(url=magnet["href"])
            rows.append(
                SearchResult(
                    provider=self.config.name, release_id=rid, title=title, size=size, details_url=url
                )
            )
        if not rows and soup.select_one("#index") is None:
            raise ProviderError(Outcome.PARSER_BROKEN)
        return rows

    def fetch_details(self, release):
        return self.enricher.fetch_details(release)

    def fetch_file_list(self, release):
        return self.enricher.fetch_file_list(release)


def make_provider(config):
    return {
        "torznab": TorznabProvider,
        "json": JSONProvider,
        "nnmclub": NNMClubProvider,
        "rutor": RutorProvider,
    }[config.protocol](config)
