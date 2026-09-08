import hashlib
import json
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from defusedxml import ElementTree

from mae.config import ProviderConfig
from mae.domain import DownloadHandle, Evidence, Outcome, ProviderError, ReleaseDetails, SearchResult
from mae.security import SafeHTTP, clean_text


class TorznabProvider:
    def __init__(self, config: ProviderConfig, http=None):
        self.config = config
        self.http = http or SafeHTTP(config.url if config.trusted_gateway else None)
        self.handles = {}
        self.descriptions = {}
        self.last_request = 0.0

    def capabilities(self):
        return {"search", "details", "download"}

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
        if self.config.source_host and self.config.details_selector and release.details_url:
            if urlsplit(release.details_url).hostname != self.config.source_host:
                raise ProviderError(Outcome.ACCESS_DENIED)
            raw = SafeHTTP().get(
                release.details_url, headers={"Cookie": self.config.cookie.get_secret_value()}
            )
            soup = BeautifulSoup(raw.decode(self.config.encoding, errors="replace"), "html.parser")
            if soup.select('input[type="password"]'):
                raise ProviderError(Outcome.AUTH_REQUIRED)
            content = soup.select_one(self.config.details_selector)
            if content is None:
                raise ProviderError(Outcome.PARSER_BROKEN)
            evidence.append(Evidence(id="card", text=clean_text(str(content)), method="source_card"))
        return ReleaseDetails(evidence=evidence)

    def fetch_file_list(self, release):
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


def make_provider(config):
    return {"torznab": TorznabProvider, "json": JSONProvider}[config.protocol](config)
