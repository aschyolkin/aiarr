"""Persistent, authenticated operational settings shared by API and worker."""

import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, SecretStr, ValidationError

from mae.config import ProviderConfig, Settings

EDITABLE = frozenset({
    "policy", "providers", "qbit_url", "qbit_username", "qbit_password",
    "jellyfin_url", "jellyfin_key", "seerr_url", "seerr_key", "tmdb_key",
    "deepseek_key", "deepseek_model", "deepseek_daily_calls",
    "deepseek_daily_tokens", "deepseek_request_calls", "deepseek_request_tokens",
    "max_attempts", "enrichment_limit", "reserve_bytes", "max_downloads",
    "stall_seconds", "backup_download_limit", "min_seeders", "min_download_speed", "max_eta_seconds",
    "upgrade_check_hours", "auto_upgrade", "speed_schedule_enabled", "speed_limited_from_hour",
    "speed_limited_to_hour", "speed_limit_download", "speed_limit_upload", "utc_offset_hours",
    "cleanup_after_days", "cleanup_ratio", "card_cache_hours",
})
SECRETS = frozenset({"qbit_password", "jellyfin_key", "seerr_key", "tmdb_key", "deepseek_key"})
PROVIDER_SECRETS = frozenset({"api_key", "cookie"})


def plain(value):
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, BaseModel):
        return {name: plain(getattr(value, name)) for name in type(value).model_fields}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    return value


class RuntimeConfig:
    def __init__(self, base: Settings):
        self.base = base
        self.path = base.state_dir / "runtime-config.json"
        self.lock_path = base.state_dir / "runtime-config.lock"

    def _read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return b"", {}
        if len(raw) > 256_000:
            raise ValueError("CONFIG_TOO_LARGE")
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) - EDITABLE:
            raise ValueError("INVALID_RUNTIME_CONFIG")
        return raw, data

    def load(self):
        _, overrides = self._read()
        return Settings.model_validate({**plain(self.base), **overrides})

    def snapshot(self):
        raw, overrides = self._read()
        settings = Settings.model_validate({**plain(self.base), **overrides})
        values = {name: plain(getattr(settings, name)) for name in EDITABLE}
        for name in SECRETS:
            values[name + "_configured"] = bool(values.pop(name))
        providers = values["providers"]
        for provider in providers:
            for name in PROVIDER_SECRETS:
                provider[name + "_configured"] = bool(provider.pop(name))
        return {"revision": hashlib.sha256(raw).hexdigest(), "settings": values}

    def save(self, revision: str, changes: dict):
        if not isinstance(changes, dict) or set(changes) - EDITABLE:
            raise ValueError("INVALID_CONFIG_FIELDS")
        self.base.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600), "r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            raw, _ = self._read()
            if revision != hashlib.sha256(raw).hexdigest():
                raise ValueError("CONFIG_CHANGED_RELOAD")
            current = self.load()
            values = {name: plain(getattr(current, name)) for name in EDITABLE}
            for name, value in changes.items():
                if name in SECRETS and value == "":
                    continue
                if name == "providers":
                    if not isinstance(value, list):
                        raise ValueError("INVALID_PROVIDERS")
                    old = {provider["name"]: provider for provider in values["providers"]}
                    merged = []
                    for provider in value:
                        if not isinstance(provider, dict) or set(provider) - set(ProviderConfig.model_fields):
                            raise ValueError("INVALID_PROVIDER_FIELDS")
                        provider = dict(provider)
                        previous = old.get(provider.get("name"), {})
                        for secret in PROVIDER_SECRETS:
                            if provider.get(secret, "") == "":
                                provider[secret] = previous.get(secret, "")
                        merged.append(provider)
                    value = merged
                values[name] = value
            try:
                validated = Settings.model_validate({**plain(self.base), **values})
            except ValidationError as exc:
                raise ValueError("INVALID_CONFIG_VALUES") from exc
            payload = {name: plain(getattr(validated, name)) for name in EDITABLE}
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
            if len(encoded) > 256_000:
                raise ValueError("CONFIG_TOO_LARGE")
            temp_fd, temp_name = tempfile.mkstemp(prefix=".runtime-config-", dir=self.base.state_dir)
            try:
                with os.fdopen(temp_fd, "wb") as temp:
                    os.fchmod(temp.fileno(), 0o600)
                    temp.write(encoded)
                    temp.flush()
                    os.fsync(temp.fileno())
                os.replace(temp_name, self.path)
                directory_fd = os.open(self.base.state_dir, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return self.snapshot()
