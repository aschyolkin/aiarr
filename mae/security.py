import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from mae.domain import Outcome, ProviderError


def clean_text(value: str) -> str:
    value = value[:128000]
    # BBCode formatting has no semantic authority; preserve its text and boundaries.
    value = re.sub(r"(?i)\[/?(?:b|i|u|s|size|color|font|url)(?:=[^\]]{0,1000})?\]", "", value)
    value = re.sub(r"(?i)\[/?(?:quote|code|spoiler|list|\*)(?:=[^\]]{0,1000})?\]", "\n", value)
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "iframe", "form", "object"]):
        tag.decompose()
    for tag in soup.find_all("br"):
        # Malformed forum HTML may nest the rest of a post inside <br>.
        # Replacing the node would discard all its technical evidence.
        tag.insert_before("\n")
        tag.unwrap()
    for tag in soup.find_all(["p", "div", "pre", "li", "tr", "blockquote", "h1", "h2", "h3"]):
        tag.insert_before("\n")
        tag.insert_after("\n")
    for tag in soup.find_all(["td", "th"]):
        tag.insert_before(" ")
        tag.insert_after(" ")
    # Inline markup must not split 'Audio: Russian' into unrelated lines.
    text = soup.get_text("", strip=False)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    text = re.sub(r"(?i)(?:https?://|magnet:)[^\s<>\"']+", "[URL removed]", text)
    text = re.sub(
        r"(?im)^.*(?:cookie|authorization|passkey|api.?key|password|token|email|e-mail).*$",
        "[redacted]",
        text,
    )
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b", "[email removed]", text)
    return text[:32000]


def origin(url):
    p = urlsplit(url)
    return p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80)


class SafeHTTP:
    """Source requests pin a validated public IP. Redirects are never followed.

    Configured infrastructure origins may be private. Cookies only go to exact
    configured origins; no upstream exception body or URL enters error messages.
    """

    def __init__(self, trusted_origin=None):
        self.trusted_origin = origin(trusted_origin) if trusted_origin else None

    def get(self, url, *, params=None, headers=None, max_bytes=2_000_000):
        p = urlsplit(url)
        if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
            raise ProviderError(Outcome.ACCESS_DENIED)
        extensions = {}
        request_headers = dict(headers or {})
        if origin(url) != self.trusted_origin:
            if p.scheme != "https":
                raise ProviderError(Outcome.ACCESS_DENIED)
            try:
                addresses = socket.getaddrinfo(p.hostname, p.port or 443, type=socket.SOCK_STREAM)
            except OSError:
                raise ProviderError(Outcome.TEMPORARY_FAILURE) from None
            ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
            if not ips or any(not ip.is_global for ip in ips):
                raise ProviderError(Outcome.ACCESS_DENIED)
            ip = str(ips[0])
            host = f"[{ip}]" if ":" in ip else ip
            target = f"{host}:{p.port}" if p.port else host
            request_headers["Host"] = p.netloc
            extensions["sni_hostname"] = p.hostname
            url = urlunsplit((p.scheme, target, p.path, p.query, ""))
        try:
            with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
                with client.stream(
                    "GET", url, params=params, headers=request_headers, extensions=extensions
                ) as response:
                    code = response.status_code
                    if code == 429:
                        import time
                        from email.utils import parsedate_to_datetime

                        raw = response.headers.get("Retry-After", "60")
                        try:
                            delay = (
                                float(raw)
                                if raw.isdigit()
                                else parsedate_to_datetime(raw).timestamp() - time.time()
                            )
                        except (ValueError, TypeError):
                            delay = 60
                        raise ProviderError(Outcome.RATE_LIMITED, delay)
                    if code in (401, 403):
                        raise ProviderError(Outcome.AUTH_REQUIRED if code == 401 else Outcome.ACCESS_DENIED)
                    if 300 <= code < 400:
                        raise ProviderError(Outcome.ACCESS_DENIED)
                    if code >= 400:
                        raise ProviderError(Outcome.TEMPORARY_FAILURE)
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise ProviderError(Outcome.PARSER_BROKEN)
                    return bytes(body)
        except httpx.HTTPError:
            raise ProviderError(Outcome.TEMPORARY_FAILURE) from None
