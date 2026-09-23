import json

import httpx
from fastapi.testclient import TestClient

from mae.api import create_app
from mae.runtime_config import RuntimeConfig

BASE = "http://prowlarr.test:30050"
GATEWAY = {"name": "bigfan", "protocol": "torznab", "url": BASE + "/8/api", "api_key": "k" * 32,
           "trusted_gateway": True}


def client_for(settings, db):
    config = RuntimeConfig(settings)
    config.save(config.snapshot()["revision"], {"providers": [GATEWAY]})
    client = TestClient(create_app(settings, db))
    client.headers["X-Api-Key"] = settings.api_key.get_secret_value()
    return client


def indexer(indexer_id, name, login):
    fields = [{"name": "baseUrl", "value": "https://x/"}]
    if login:
        fields += [{"name": "username", "value": "me"}, {"name": "password", "value": "secret"}]
    return {"id": indexer_id, "name": name, "privacy": "semiPrivate", "enable": True, "fields": fields}


def test_indexers_list_links_sources_and_hides_passwords(settings, db, respx_mock):
    client = client_for(settings, db)
    respx_mock.get(BASE + "/api/v1/indexer").mock(return_value=httpx.Response(
        200, json=[indexer(8, "BigFANGroup", False), indexer(11, "Kinozal", True)]))
    respx_mock.get(BASE + "/api/v1/indexerstatus").mock(return_value=httpx.Response(
        200, json=[{"indexerId": 11, "disabledTill": "2026-09-24T11:43:50Z", "initialFailure": "2026-09-09T12:35:49Z"}]))
    rows = {row["name"]: row for row in client.get("/admin/indexers").json()}
    assert rows["BigFANGroup"]["source"] == "bigfan" and not rows["BigFANGroup"]["needs_login"]
    kinozal = rows["Kinozal"]
    assert kinozal["source"] is None and kinozal["needs_login"] and kinozal["username"] == "me"
    assert kinozal["disabled_till"] == "2026-09-24T11:43:50Z"
    assert "secret" not in json.dumps(rows)


def test_credentials_are_validated_by_prowlarr_before_saving(settings, db, respx_mock):
    client = client_for(settings, db)
    respx_mock.get(BASE + "/api/v1/indexer/11").mock(return_value=httpx.Response(200, json=indexer(11, "Kinozal", True)))
    put = respx_mock.put(BASE + "/api/v1/indexer/11").mock(return_value=httpx.Response(
        400, json=[{"errorMessage": "Unable to login"}]))
    response = client.put("/admin/indexers/11/credentials", json={"username": "new", "password": "pw"})
    assert response.status_code == 400 and "Unable to login" in response.json()["detail"]
    sent = {f["name"]: f.get("value") for f in json.loads(put.calls[0].request.content)["fields"]}
    assert sent["username"] == "new" and sent["password"] == "pw"
    put.mock(return_value=httpx.Response(202, json={}))
    assert client.put("/admin/indexers/11/credentials", json={"username": "new"}).json() == {"saved": True}
    kept = {f["name"]: f.get("value") for f in json.loads(put.calls[1].request.content)["fields"]}
    assert kept["password"] == "secret"  # blank password keeps the stored one


def test_attach_adds_torznab_source_with_gateway_key(settings, db, respx_mock):
    client = client_for(settings, db)
    respx_mock.get(BASE + "/api/v1/indexer/12").mock(return_value=httpx.Response(
        200, json=indexer(12, "RuTracker.org", True)))
    assert client.post("/admin/indexers/12/attach").json()["source"] == "rutracker-org"
    providers = RuntimeConfig(settings).load().providers
    added = next(p for p in providers if p.name == "rutracker-org")
    assert added.url == BASE + "/12/api" and added.trusted_gateway
    assert added.api_key.get_secret_value() == "k" * 32
    assert client.post("/admin/indexers/12/attach").status_code == 409


def test_mirror_switch_only_to_known_addresses(settings, db, respx_mock):
    client = client_for(settings, db)
    data = indexer(11, "Kinozal", True)
    data["indexerUrls"] = ["https://kinozal.me/", "https://kinozal.guru/"]
    data["fields"][0] = {"name": "baseUrl", "value": "https://kinozal.me/"}
    respx_mock.get(BASE + "/api/v1/indexer/11").mock(return_value=httpx.Response(200, json=data))
    put = respx_mock.put(BASE + "/api/v1/indexer/11").mock(return_value=httpx.Response(202, json={}))
    assert client.put("/admin/indexers/11/mirror", json={"base_url": "https://evil.test/"}).status_code == 400
    assert not put.called
    assert client.put("/admin/indexers/11/mirror", json={"base_url": "https://kinozal.guru"}).json()["saved"]
    sent = {f["name"]: f.get("value") for f in json.loads(put.calls[0].request.content)["fields"]}
    assert sent["baseUrl"] == "https://kinozal.guru/"
