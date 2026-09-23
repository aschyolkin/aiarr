# Media Acquisition Engine

Self-hosted Python engine using Seerr, qBittorrent and Jellyfin. The recorded
TrueNAS deployment replaced Radarr/Sonarr in production on 2026-09-23. See the
[acceptance record](docs/ACCEPTANCE.md), [Seerr contract](docs/SEERR_CONTRACT.md)
and [source matrix](docs/SOURCE_SUPPORT_MATRIX.md) for supported sources and
remaining limits.

Implemented: authenticated FastAPI facade, persistent SQLite jobs/migrations,
single durable worker, Torznab and owned JSON protocol adapters, evidence parsing,
deterministic constraints/scoring, restricted DeepSeek adapter, qBittorrent client,
per-file ffprobe checks, journaled hardlink import and Jellyfin discovery check.
Default dry-run makes no downloads or library modifications. Unknown mandatory
properties and LLM-only interpretations wait for review.

Production administration: `http://10.110.111.2:30280/admin`. Enter the aiarr
API key, select an order to see its timeline, torrent progress and episode
coverage, or open **Настройки aiarr** to edit sources, quality, integrations,
queue limits and the DeepSeek key. Torznab sources use an API key; protected
source cards can use a Cookie; anonymous sources leave both fields empty.
Empty secret fields keep existing values. Settings are stored in the private
aiarr state dataset and picked up by the worker within ten seconds. The API
key used to log in, storage paths and the production dry-run switch remain in
the TrueNAS app configuration.

```
uv sync --frozen
uv run pytest -q
uv run ruff check mae migrations tests
```

[Deployment/rollback](docs/DEPLOYMENT.md) · [Reuse audit](docs/REUSE_PLAN.md) ·
[Architecture decision](docs/ADR-001-core.md) · [Adapter SDK](docs/ADAPTER_SDK.md)

Do not commit `.env`, private source pages, cookies, passkeys or account data.
