import json

from mae.config import Settings


def test_anonymous_provider_environment_example_is_dry_run_and_secret_free(monkeypatch):
    monkeypatch.setenv("MAE_API_KEY", "offline-config-key-000000000000")
    monkeypatch.setenv(
        "MAE_PROVIDERS",
        json.dumps(
            [
                {
                    "name": "rutor",
                    "protocol": "rutor",
                    "url": "https://rutor.info",
                    "source_host": "rutor.info",
                    "details_adapter": "rutor",
                },
                {
                    "name": "nnmclub",
                    "protocol": "nnmclub",
                    "url": "https://nnmclub.to",
                    "encoding": "windows-1251",
                },
            ]
        ),
    )

    settings = Settings(_env_file=None)

    assert settings.dry_run is True
    assert [provider.name for provider in settings.providers] == ["rutor", "nnmclub"]
    assert all(not provider.cookie.get_secret_value() for provider in settings.providers)
    assert all(not provider.api_key.get_secret_value() for provider in settings.providers)
