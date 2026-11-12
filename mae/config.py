import codecs
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_exact_https_origin(parsed, host):
    return (
        parsed.scheme == "https"
        and parsed.hostname == host
        and parsed.port in (None, 443)
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
        and not parsed.username
        and not parsed.password
    )


class ProviderConfig(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    protocol: Literal["torznab", "json", "nnmclub", "rutor"] = "torznab"
    url: str
    api_key: SecretStr = SecretStr("")
    # Explicit trust only for the configured gateway, never arbitrary source URLs.
    trusted_gateway: bool = False
    source_host: str | None = None
    # Opt-in for an operator-controlled source resolver, e.g. a Fake-IP proxy.
    # It never applies to a redirect or to any other host.
    trusted_source: bool = False
    details_selector: str | None = None
    details_adapter: Literal["html", "rutor"] = "html"
    cookie: SecretStr = SecretStr("")
    encoding: str = "utf-8"
    interval: float = Field(default=2, ge=0, le=300)
    # Torznab categories to restrict every search to (e.g. 2000 movies, 5000 TV);
    # empty searches all. Trackers misfile releases, so this is opt-in per source.
    categories: list[int] = Field(default=[], max_length=20)

    @model_validator(mode="after")
    def validate_enrichment(self):
        if self.details_adapter == "rutor":
            selector = "table#details > tr:first-child > td:last-child, table#details > tbody > tr:first-child > td:last-child"
            if self.details_selector not in (None, selector):
                raise ValueError("Rutor adapter owns its card selector")
            self.details_selector = selector
        if bool(self.source_host) != bool(self.details_selector):
            raise ValueError("source_host and details_selector must be configured together")
        if self.trusted_source and not self.source_host:
            raise ValueError("trusted_source requires source_host")
        try:
            codecs.lookup(self.encoding)
        except LookupError:
            raise ValueError("Unknown source encoding") from None
        if self.details_selector:
            try:
                BeautifulSoup("", "html.parser").select(self.details_selector)
            except Exception:
                raise ValueError("Invalid details_selector") from None
        parsed = urlsplit(self.url)
        if self.protocol == "rutor":
            if (
                not _is_exact_https_origin(parsed, "rutor.info")
                or self.source_host != "rutor.info"
                or self.details_adapter != "rutor"
            ):
                raise ValueError("rutor requires its exact HTTPS source_host and rutor details adapter")
        if self.protocol == "nnmclub" and (
            not _is_exact_https_origin(parsed, "nnmclub.to") or self.source_host
        ):
            raise ValueError("nnmclub requires the exact HTTPS nnmclub.to origin")
        return self


class Policy(BaseModel):
    version: str = "base-1"
    russian_required: bool = True
    max_bytes: int = Field(default=60 * 1024**3, gt=0)
    max_file_bytes: int = Field(default=20 * 1024**3, gt=0)
    resolutions: list[int] = [2160, 1080]
    # Accepted only when no release in a preferred resolution qualifies.
    fallback_resolutions: list[int] = [720]
    codecs: list[str] = ["H.265", "H.264"]
    source_weights: dict[str, int] = {"Web": 40, "Blu-ray": 40}
    # Only explicitly bad sources are rejected; an unrecognized/unlabeled
    # source (guessit returns None, or an unmapped word like "Hybrid") is
    # accepted for review rather than assumed to be a cam/telesync rip.
    denied_sources: list[str] = [
        "Camera", "HD Camera", "Telesync", "HD Telesync", "Telecine", "HD Telecine",
    ]
    studio_weights: dict[str, int] = {
        "LostFilm": 0,
        "NewStudio": 0,
        "AlexFilm": 0,
        "Red Head Sound": 0,
        "Flarrow Films": 0,
    }
    # Kind of Russian voice-over; the best one present in a release scores.
    voice_weights: dict[str, int] = {"Dub": 15, "MVO": 10, "DVO": 5, "AVO": 5, "VO": 0}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAE_", env_file=".env", extra="ignore")
    api_key: SecretStr = Field(min_length=24)
    state_dir: Path = Path("state")
    store: Path = Path("/data/store")
    dry_run: bool = True
    providers: list[ProviderConfig] = []
    policy: Policy = Policy()
    qbit_url: str = "http://127.0.0.1:30024"
    qbit_username: str = ""
    qbit_password: SecretStr = SecretStr("")
    jellyfin_url: str = "http://127.0.0.1:30013"
    jellyfin_key: SecretStr = SecretStr("")
    seerr_url: str = "http://127.0.0.1:30357"
    seerr_key: SecretStr = SecretStr("")
    tmdb_key: SecretStr = SecretStr("")
    deepseek_key: SecretStr = SecretStr("")
    deepseek_model: Literal["deepseek-flash"] = "deepseek-flash"
    deepseek_daily_calls: int = Field(default=20, ge=0)
    deepseek_daily_tokens: int = Field(default=200000, ge=0)
    deepseek_request_calls: int = Field(default=6, ge=0)
    deepseek_request_tokens: int = Field(default=60000, ge=0)
    max_attempts: int = Field(default=5, ge=1, le=10)
    enrichment_limit: int = Field(default=20, ge=1, le=100)
    reserve_bytes: int = Field(default=5 * 1024**3, ge=0)
    max_downloads: int = Field(default=2, ge=1, le=10)
    stall_seconds: int = Field(default=180, ge=60, le=86400)
    backup_download_limit: int = Field(default=100 * 1024, ge=1024)
    min_seeders: int = Field(default=2, ge=0, le=1000)
    min_download_speed: int = Field(default=125_000, ge=0)  # bytes/s, 1 Mbit/s
    max_eta_seconds: int = Field(default=3 * 3600, ge=600)
    # Re-search available movies and series for a better release every N hours; 0 = never.
    upgrade_check_hours: int = Field(default=24, ge=0, le=24 * 90)
    # Replace without asking once a clearly better release is found.
    auto_upgrade: bool = False
    # Download the best release even when some facts are unconfirmed; ffprobe decides afterwards.
    trust_unproven: bool = True
    # qBittorrent alternative speed limits between these local hours.
    speed_schedule_enabled: bool = False
    speed_limited_from_hour: int = Field(default=8, ge=0, le=23)
    speed_limited_to_hour: int = Field(default=1, ge=0, le=23)
    speed_limit_download: int = Field(default=2 * 1024**2, ge=1024)  # bytes/s
    speed_limit_upload: int = Field(default=512 * 1024, ge=1024)  # bytes/s
    utc_offset_hours: int = Field(default=3, ge=-12, le=14)
    # Remove a stopped, fully imported torrent from qBittorrent after N days; 0 = keep.
    cleanup_after_days: int = Field(default=0, ge=0, le=3650)
    # ... or once its share ratio reaches this value; 0 = ignore the ratio.
    cleanup_ratio: float = Field(default=0, ge=0, le=100)
    # Keep fetched release cards this long; hourly retries and upgrade checks
    # re-read the same cards. 0 = always fetch.
    card_cache_hours: int = Field(default=168, ge=0, le=24 * 90)

    @model_validator(mode="after")
    def validate_names(self):
        names = [p.name for p in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("provider names must be unique")
        if self.store != Path("/data/store"):
            raise ValueError("media mount must be /data/store")
        return self
