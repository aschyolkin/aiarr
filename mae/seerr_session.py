"""Validate the browser's existing Seerr session for the local admin UI."""

from http.cookies import CookieError, SimpleCookie

import httpx


def seerr_admin_session(settings, cookie_header):
    if not cookie_header or len(cookie_header) > 8192:
        return None
    cookies = SimpleCookie()
    try:
        cookies.load(cookie_header)
    except CookieError:
        return None
    session = cookies.get("connect.sid")
    if session is None or not session.value or len(session.value) > 4096:
        return None
    with httpx.Client(
        base_url=settings.seerr_url,
        timeout=5,
        trust_env=False,
        follow_redirects=False,
        headers={"Cookie": f"connect.sid={session.value}"},
    ) as client:
        response = client.get("/api/v1/auth/me")
    if response.status_code in (401, 403):
        return None
    response.raise_for_status()
    user = response.json()
    if not isinstance(user, dict) or type(user.get("id")) is not int or type(user.get("permissions")) is not int:
        return None
    # Seerr Permission.ADMIN = 2. Other users must not receive integration
    # credentials, other people's requests, or destructive controls.
    if not user["permissions"] & 2:
        return None
    return {"id": user["id"], "name": user.get("displayName") or user.get("username") or "Seerr"}


def same_origin_write(request):
    """Reject cookie-authenticated cross-origin writes, including form CSRF."""
    from urllib.parse import urlsplit

    origin = request.headers.get("Origin", "")
    parsed = urlsplit(origin)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc == request.headers.get("Host", "")
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and request.headers.get("X-Aiarr-UI") == "1"
    )
