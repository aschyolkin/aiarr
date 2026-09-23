from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Outcome(StrEnum):
    NO_RESULTS = "NO_RESULTS"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ACCESS_DENIED = "ACCESS_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    PARSER_BROKEN = "PARSER_BROKEN"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"


class ProviderError(Exception):
    def __init__(self, outcome: Outcome, retry_after: float = 60):
        super().__init__(outcome.value)
        self.outcome = outcome
        self.retry_after = max(1, min(retry_after, 86400))


class Evidence(BaseModel):
    id: str
    text: str = Field(max_length=32000)
    method: str
    observed_at: str = Field(default_factory=utcnow)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["audio_language", "studio", "video_codec", "resolution"]
    value: str
    evidence_id: str
    quote: str = Field(min_length=1, max_length=500)
    method: str = "rule"


class Work(BaseModel):
    kind: Literal["movie", "series"]
    external_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=250)
    aliases: list[str] = []
    year: int | None = None
    original_language: str | None = None
    seasons: list[int] = []
    # Keys SxxExx, desired coverage from metadata, never inferred from a pack.
    episodes: list[str] = []
    # Transient search context: monitored episodes already confirmed in library.
    covered_episodes: list[str] = []


class SearchResult(BaseModel):
    provider: str
    release_id: str
    title: str = Field(max_length=1000)
    size: int | None = Field(default=None, ge=0)
    published_at: str | None = None
    seeders: int | None = Field(default=None, ge=0)
    details_url: str | None = None
    infohash: str | None = None


class ReleaseDetails(BaseModel):
    evidence: list[Evidence] = []
    files: list[str] = []
    image_url: str | None = Field(default=None, max_length=1000)


class DownloadHandle(BaseModel):
    # Deliberately separate from candidate/evidence serialization.
    url: SecretStr


class Candidate(BaseModel):
    release: SearchResult
    evidence: list[Evidence] = []
    claims: list[Claim] = []
    parsed: dict = {}
    reasons: list[str] = []
    unknown: list[str] = []
    score: dict[str, int] = {}
    image_url: str | None = Field(default=None, max_length=1000)


class Provider(Protocol):
    def capabilities(self) -> set[str]: ...
    def healthcheck(self) -> dict: ...
    def authentication_status(self) -> dict: ...
    def search(self, query: Work) -> list[SearchResult]: ...
    def fetch_details(self, release: SearchResult) -> ReleaseDetails: ...
    def fetch_file_list(self, release: SearchResult) -> list[str]: ...
    def resolve_download(self, release: SearchResult) -> DownloadHandle: ...
