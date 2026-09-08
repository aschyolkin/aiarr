# Media Acquisition Engine

Experimental self-hosted Python engine retaining Seerr, qBittorrent and Jellyfin.
**Not yet a production replacement for Radarr/Sonarr.** See the explicit gaps in
[acceptance](docs/ACCEPTANCE.md), [Seerr contract](docs/SEERR_CONTRACT.md) and
[source matrix](docs/SOURCE_SUPPORT_MATRIX.md).

Implemented: authenticated FastAPI facade, persistent SQLite jobs/migrations,
single durable worker, Torznab and owned JSON protocol adapters, evidence parsing,
deterministic constraints/scoring, restricted DeepSeek adapter, qBittorrent client,
per-file ffprobe checks, journaled hardlink import and Jellyfin discovery check.
Default dry-run makes no downloads or library modifications. Unknown mandatory
properties and LLM-only interpretations wait for review.

```
uv sync --frozen
uv run pytest -q
uv run ruff check mae migrations tests
```

[Deployment/rollback](docs/DEPLOYMENT.md) · [Reuse audit](docs/REUSE_PLAN.md) ·
[Architecture decision](docs/ADR-001-core.md) · [Adapter SDK](docs/ADAPTER_SDK.md)

Do not commit `.env`, private source pages, cookies, passkeys or account data.
