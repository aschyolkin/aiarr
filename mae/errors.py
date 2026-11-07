"""Bounded failure codes for unexpected exceptions.

A job reason must name the failing service and status, never a URL or body.
An outage of the operator's own services (Jellyfin, Seerr, qBittorrent) says
nothing about the job itself, so it must not consume the job's attempts.
"""

from urllib.parse import urlsplit

import httpx


def _origin(url):
    parts = urlsplit(str(url))
    return (parts.hostname or "").lower(), parts.port or (443 if parts.scheme == "https" else 80)


def _service(settings, url):
    origin = _origin(url)
    for name, base in (
        ("JELLYFIN", settings.jellyfin_url),
        ("SEERR", settings.seerr_url),
        ("QBITTORRENT", settings.qbit_url),
    ):
        if base and _origin(base) == origin:
            return name
    return None


def failure(exc, settings):
    """Return (code, dependency): dependency is True for an operator service outage."""
    if not isinstance(exc, httpx.HTTPError):
        return type(exc).__name__, False
    try:
        service = _service(settings, exc.request.url)
    except RuntimeError:  # an httpx error raised without an attached request
        service = None
    if isinstance(exc, httpx.HTTPStatusError):
        detail = f"HTTP_{exc.response.status_code}"
    elif isinstance(exc, httpx.TimeoutException):
        detail = "TIMEOUT"
    elif isinstance(exc, httpx.TransportError):
        detail = "UNREACHABLE"
    else:
        detail = type(exc).__name__.upper()
    return f"{service or 'UPSTREAM'}_{detail}", service is not None
