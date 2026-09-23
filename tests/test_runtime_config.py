import json
import stat

from fastapi.testclient import TestClient

from mae.api import create_app
from mae.config import Settings
from mae.runtime_config import RuntimeConfig


def test_persistent_config_redacts_secrets_and_reloads(settings):
    base = settings.model_copy(update={"seerr_key": "private-seerr-key"})
    store = RuntimeConfig(base)
    before = store.snapshot()
    assert "private-seerr-key" not in json.dumps(before)
    assert before["settings"]["seerr_key_configured"]
    updated = store.save(before["revision"], {
        "max_downloads": 3,
        "seerr_key": "",
        "providers": [{"name": "example", "protocol": "torznab", "url": "https://example.com/",
                       "api_key": "private-provider-key"}],
    })
    assert updated["settings"]["max_downloads"] == 3
    assert "private-provider-key" not in json.dumps(updated)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    restarted = RuntimeConfig(base).load()
    assert restarted.max_downloads == 3
    assert restarted.seerr_key.get_secret_value() == "private-seerr-key"
    assert restarted.providers[0].api_key.get_secret_value() == "private-provider-key"
    store.save(updated["revision"], {"providers": [{"name": "example", "protocol": "torznab",
                                                 "url": "https://example.com/", "api_key": ""}]})
    assert store.load().providers[0].api_key.get_secret_value() == "private-provider-key"


def test_config_api_auth_conflict_and_validation(settings, db):
    client = TestClient(create_app(settings, db))
    assert client.get("/admin/config").status_code == 401
    client.headers["X-Api-Key"] = settings.api_key.get_secret_value()
    first = client.get("/admin/config").json()
    change = {"revision": first["revision"], "settings": {"max_downloads": 4}}
    assert client.put("/admin/config", json=change).status_code == 200
    assert client.get("/admin/config").json()["settings"]["max_downloads"] == 4
    assert client.put("/admin/config", json=change).status_code == 409
    revision = client.get("/admin/config").json()["revision"]
    assert client.put("/admin/config", json={"revision": revision,
                                                "settings": {"api_key": "not-allowed"}}).status_code == 422
    assert client.put("/admin/config", json={"revision": revision,
                                                "settings": {"max_downloads": 0}}).status_code == 422
    assert client.get("/admin/config").json()["settings"]["max_downloads"] == 4


def test_config_does_not_change_bootstrap_or_store(settings):
    store = RuntimeConfig(settings)
    revision = store.snapshot()["revision"]
    for field, value in (("api_key", "changed"), ("store", "/tmp/other"),
                         ("state_dir", "/tmp/other"), ("dry_run", False)):
        try:
            store.save(revision, {field: value})
        except ValueError as exc:
            assert str(exc) == "INVALID_CONFIG_FIELDS"
        else:
            raise AssertionError(field)
    assert Settings.model_validate(store.load().model_dump()).store == settings.store


def test_anonymous_source_removal_and_deepseek_key_change(settings):
    store = RuntimeConfig(settings)
    first = store.snapshot()
    second = store.save(first["revision"], {
        "providers": [{"name": "anonymous", "protocol": "torznab", "url": "https://example.com/"}],
        "deepseek_key": "deepseek-private-one",
    })
    assert second["settings"]["providers"][0]["api_key_configured"] is False
    assert second["settings"]["deepseek_key_configured"] is True
    third = store.save(second["revision"], {"providers": [], "deepseek_key": "deepseek-private-two"})
    assert third["settings"]["providers"] == []
    assert store.load().deepseek_key.get_secret_value() == "deepseek-private-two"
    assert "deepseek-private-two" not in json.dumps(third)
