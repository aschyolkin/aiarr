"""Separate bounded, authenticated HTML/BBCode card enrichment."""

import re
import time
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from mae.domain import Evidence, Outcome, ProviderError, ReleaseDetails
from mae.security import SafeHTTP, clean_text


def card_image(content, base_url):
    """First poster-like HTTPS image of a release card, for display only.

    Never used as evidence. BBCode forums put lazy images in var.postImg[title].
    """
    for node in content.select("img[src], var.postImg[title]"):
        value = node.get("src") if node.name == "img" else node.get("title")
        url = urljoin(base_url, (value or "").strip())
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or len(url) > 1000 or re.search(r"[\s\"'<>]", url):
            continue
        if re.search(r"(?i)smil|emoji|icon|avatar|rank|button|spacer|/images/", parsed.path):
            continue
        try:
            if 0 < int(node.get("width") or 999) < 80 or 0 < int(node.get("height") or 999) < 80:
                continue
        except ValueError:
            pass
        return url
    return None


class HTMLDetailsEnricher:
    def __init__(self, config, http=None):
        self.config = config
        self.http = http or SafeHTTP(f"https://{config.source_host}" if config.trusted_source else None)
        self.cache = {}
        self.last_request = 0.0

    def fetch_details(self, release):
        url = release.details_url
        if not url:
            raise ProviderError(Outcome.UNSUPPORTED_CAPABILITY)
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != self.config.source_host
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ProviderError(Outcome.ACCESS_DENIED)
        cached = self.cache.get(url)
        if cached and cached[0] > time.monotonic():
            return cached[1].model_copy(deep=True)
        delay = self.config.interval - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        raw = self.http.get(url, headers={"Cookie": self.config.cookie.get_secret_value()})
        result = self.parse(raw, url)
        if len(self.cache) >= 64:
            self.cache.pop(next(iter(self.cache)))
        self.cache[url] = (time.monotonic() + 300, result)
        return result.model_copy(deep=True)

    def parse(self, raw, url):
        try:
            body = raw.decode(self.config.encoding, errors="strict")
        except UnicodeError:
            raise ProviderError(Outcome.PARSER_BROKEN) from None
        soup = BeautifulSoup(body, "html.parser")
        content = soup.select_one(self.config.details_selector)
        if content is None:
            if soup.select_one('input[type="password"]'):
                raise ProviderError(Outcome.AUTH_REQUIRED)
            raise ProviderError(Outcome.PARSER_BROKEN)
        if content.select_one('input[type="password"]'):
            raise ProviderError(Outcome.AUTH_REQUIRED)
        cleaned = clean_text(str(content))
        if not cleaned:
            raise ProviderError(Outcome.PARSER_BROKEN)
        result = ReleaseDetails(
            evidence=[Evidence(id="card", text=cleaned, method="html-card-v1")],
            image_url=card_image(content, url),
        )
        return result


class RutorDetailsEnricher(HTMLDetailsEnricher):
    """Concrete Rutor card layout, checked against public topic 384544."""

    def __init__(self, config, http=None):
        super().__init__(config, http)
        self.file_urls = {}

    def parse(self, raw, url):
        result = super().parse(raw, url)
        result.evidence[0].method = "rutor-card-v1"
        topic = re.match(r"^/torrent/(\d+)(?:/|$)", urlsplit(url).path)
        if not topic:
            raise ProviderError(Outcome.PARSER_BROKEN)
        endpoint = f"/descriptions/{topic[1]}.files"
        self.file_urls.pop(url, None)
        body = raw.decode(self.config.encoding)
        if re.search(r"\.load\(\s*['\"]" + re.escape(endpoint) + r"['\"]\s*\)", body):
            if len(self.file_urls) >= 64:
                self.file_urls.pop(next(iter(self.file_urls)))
            self.file_urls[url] = f"https://{self.config.source_host}{endpoint}"
        return result

    def fetch_file_list(self, release):
        self.fetch_details(release)
        url = self.file_urls.get(release.details_url)
        if not url:
            raise ProviderError(Outcome.UNSUPPORTED_CAPABILITY)
        delay = self.config.interval - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        raw = self.http.get(url, headers={"Cookie": self.config.cookie.get_secret_value()})
        try:
            soup = BeautifulSoup(raw.decode(self.config.encoding), "html.parser")
        except UnicodeError:
            raise ProviderError(Outcome.PARSER_BROKEN) from None
        if soup.select_one('input[type="password"]'):
            raise ProviderError(Outcome.AUTH_REQUIRED)
        rows = soup.find_all("tr")
        if not rows or len(rows) > 10000:
            raise ProviderError(Outcome.PARSER_BROKEN)
        files = []
        for row in rows:
            cells = row.find_all("td", recursive=False)
            if len(cells) != 2 or cells[0].find(True):
                # A directory tree/changed schema needs its own verified mapping.
                raise ProviderError(Outcome.UNSUPPORTED_CAPABILITY)
            name = cells[0].get_text().strip()
            path = PurePosixPath(name)
            if (
                not name
                or name == "."
                or "\x00" in name
                or "\\" in name
                or path.is_absolute()
                or ".." in path.parts
            ):
                raise ProviderError(Outcome.PARSER_BROKEN)
            files.append(name)
        if len(files) != len(set(files)):
            raise ProviderError(Outcome.PARSER_BROKEN)
        return files
