from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from mae.api import create_app

ADMIN = {"id": 1, "displayName": "Admin", "permissions": 2}


def client_with(settings, db, user):
    def fake(current, cookie):
        return {"id": user["id"], "name": user["displayName"]} if user and "connect.sid=ok" in cookie else None

    patcher = patch("mae.api.seerr_admin_session", side_effect=fake)
    patcher.start()
    client = TestClient(create_app(settings, db), base_url="http://aiarr.test")
    return client, patcher


def test_seerr_admin_cookie_reads_admin_data(settings, db, movie):
    db.submit(movie)
    client, patcher = client_with(settings, db, ADMIN)
    try:
        client.cookies.set("connect.sid", "ok")
        assert client.get("/admin/session").json()["mode"] == "seerr"
        assert client.get("/admin/overview").json()["jobs"][0]["work"]
        # Cookie SSO is limited to /admin; integration endpoints still need the key.
        assert client.get("/metrics").status_code == 401
    finally:
        patcher.stop()


def test_missing_or_non_admin_session_is_rejected(settings, db):
    client, patcher = client_with(settings, db, None)
    try:
        client.cookies.set("connect.sid", "ok")
        assert client.get("/admin/overview").status_code == 401
    finally:
        patcher.stop()


def test_cookie_writes_require_same_origin_marker(settings, db, movie):
    job_id = db.submit(movie)
    client, patcher = client_with(settings, db, ADMIN)
    try:
        client.cookies.set("connect.sid", "ok")
        path = f"/admin/jobs/{job_id}/cancel"
        assert client.post(path).status_code == 403
        assert client.post(path, headers={"Origin": "http://evil.test", "X-Aiarr-UI": "1"}).status_code == 403
        assert client.post(path, headers={"Origin": "http://aiarr.test"}).status_code == 403
        assert db.job(job_id)["state"] == "REQUESTED"
        assert client.post(path, headers={"Origin": "http://aiarr.test", "X-Aiarr-UI": "1"}).status_code == 200
    finally:
        patcher.stop()


def test_session_validation_checks_admin_permission(settings):
    from mae.seerr_session import seerr_admin_session

    def respond(payload, status=200):
        return httpx.Response(status, json=payload, request=httpx.Request("GET", "http://seerr/api/v1/auth/me"))

    with patch("httpx.Client.get", return_value=respond(ADMIN)):
        assert seerr_admin_session(settings, "connect.sid=s%3Aabc")["id"] == 1
    with patch("httpx.Client.get", return_value=respond({**ADMIN, "permissions": 32})):
        assert seerr_admin_session(settings, "connect.sid=s%3Aabc") is None
    with patch("httpx.Client.get", return_value=respond({}, 401)):
        assert seerr_admin_session(settings, "connect.sid=s%3Aabc") is None
    assert seerr_admin_session(settings, "other=1") is None
