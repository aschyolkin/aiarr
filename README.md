# Media Acquisition Engine (aiarr)

**English** · [Русский](README.ru.md)

A self-hosted replacement for Radarr and Sonarr that is built around Russian-language
torrent trackers. Users make requests in [Seerr](https://github.com/seerr-team/seerr);
aiarr finds a release, downloads it with qBittorrent, verifies the actual file with
ffprobe, publishes it to the library as a hardlink and waits until Jellyfin sees it.

```
Seerr request ─► search in sources ─► pick a release ─► qBittorrent
                                                             │
Jellyfin ◄─ hardlink import ◄─ ffprobe verification ◄────────┘
```

It runs in production on a TrueNAS SCALE home server and replaced Radarr and Sonarr there
on 2026-09-23. See the [operations log](docs/OPERATIONS.ru.md) (in Russian) for the
current deployment state.

## Features

**Finding releases**
- Sources: any Torznab indexer (Prowlarr, Jackett), plus built-in anonymous adapters for
  NNM-Club and Rutor and a documented JSON protocol for your own sources
  ([Adapter SDK](docs/ADAPTER_SDK.md)).
- Sources are queried in parallel. The same torrent returned by several sources
  (matched by infohash) is a single candidate. Release cards are cached.
- Torznab indexers that declare `tmdbid`/`tvdbid` support are also searched by ID, and a
  source can be limited to Torznab categories.
- Searches use the title, a Latin-script alias (so "Рус / Eng" releases are found) and
  the English title of foreign-language works.
- Manual search by free text from a request page, with one-click download of any result.

**Choosing the right release**
- Deterministic rules decide what is acceptable: title and year, season and episodes,
  resolution (2160p/1080p preferred, 720p as a fallback), video codec, source quality
  (CAM/telesync rejected), size limits and a required Russian audio track.
- Scoring prefers the better source, resolution, H.265, dubbing over multi-voice over
  single-voice, and configurable studio weights (LostFilm, NewStudio, ...).
- Russian tracker titles and cards are parsed (`Рус / Eng (2024) [WEB-DL 1080p] | D, P2`).
- An optional, budgeted DeepSeek step interprets ambiguous cards. Every claim must quote
  the evidence it comes from, and the file is checked by ffprobe afterwards anyway.
  Without a key everything works, unclear releases just wait for review.

**Downloading**
- A runner-up release starts at a low speed beside the primary one. A dead or too slow
  torrent is replaced within minutes; a slow one is kept only as a last resort.
- Disk space is reserved up front. A queue limit, a speed schedule (alternative limits
  at set hours) and cleanup of seeded torrents (by age or share ratio) are available.
- Series: only the missing episodes are selected inside a pack. Episodes that have not
  aired yet are not searched; the request sleeps until the next air date.

**Verifying and importing**
- Each video file is probed with ffprobe: codec, resolution and a Russian audio track.
  A file that fails verification is rejected, its torrent is removed and the next release
  is tried automatically.
- Import is a journaled hardlink with device and inode checks, so a crash never leaves a
  half-published file. Jellyfin's naming (`Title (Year) [tmdbid-N]`) is used.
- Better releases are found later (movies and series) and either offered or replaced
  automatically. The old file is renamed away and restored if the new one fails.

**Operations**
- Web UI at `/admin`: request cards with progress, candidate table with the reason for
  every decision, event timeline, source health, episode calendar and all settings.
  Sign in through an open Seerr administrator session, or with the API key.
- Seerr-compatible facade (`/radarr/api/v3`, `/sonarr/api/v3`): Seerr talks to aiarr as
  if it were Radarr and Sonarr.
- Deleting a request in Seerr removes the files and torrent aiarr owns, after checking
  category, tag, path, inode and device. Nothing else is ever deleted.
- Dry-run is the default: nothing is downloaded or written to the library.

## Architecture

| Part | What it does |
|---|---|
| `mae/api.py`, `mae/admin.py` | FastAPI service: admin UI and API, Radarr/Sonarr facade |
| `mae/worker.py` | One durable worker loop: search, download monitoring, import, upgrades, cleanup |
| `mae/db.py`, `migrations/` | SQLite (WAL) job journal and Alembic migrations |
| `mae/providers.py`, `mae/enrichment.py` | Source adapters and release card parsing |
| `mae/parsing.py`, `mae/policies.py` | Evidence extraction and acceptance rules and scoring |
| `mae/downloads.py` | qBittorrent client with ownership checks on every operation |
| `mae/verification.py`, `mae/importing.py` | ffprobe checks, hardlink publishing, library paths |
| `mae/seerr.py`, `mae/jellyfin.py` | Seerr reconciliation and Jellyfin library checks |
| `mae/deepseek.py` | Optional LLM interpretation with budgets and a cache |

Two processes run from one image: `api` and `worker`, plus a one-shot `migrate`.
A file lock guarantees a single worker. State lives in one directory (`MAE_STATE_DIR`):
the database, the runtime configuration edited in the UI, and the lock.

Design notes: [ADR-001](docs/ADR-001-core.md), [Seerr contract](docs/SEERR_CONTRACT.md),
[source matrix](docs/SOURCE_SUPPORT_MATRIX.md), [acceptance record](docs/ACCEPTANCE.md).

## Requirements

- Seerr, qBittorrent (Web UI enabled), Jellyfin and at least one search source.
- One dataset mounted at `/data/store` that contains the download directory and the
  library, because import is a hardlink:
  ```
  /data/store/downloads/{films,serials}   qBittorrent saves here
  /data/store/films                       movie library
  /data/store/serials                     series library
  ```
- Docker (the provided image contains ffmpeg), or Python 3.12 with `uv` and `ffprobe`.

## Quick start

```sh
cp .env.example .env && chmod 600 .env
# set MAE_API_KEY (24+ random characters), the qBittorrent, Jellyfin and Seerr URLs and keys,
# and MAE_STATE_HOST (a persistent directory writable by uid 3000)
docker compose up -d --build        # runs migrations, then api and worker
```

The API listens on `127.0.0.1:30280` (put it behind your reverse proxy). Open
`http://<host>:30280/admin`, sign in as a Seerr administrator in the same browser and
add sources under *Sources*. `MAE_DRY_RUN=true` is the default: check that the right
releases are selected, then set it to `false`.

Finally, in Seerr add a Radarr and a Sonarr server that point to
`http://<host>:30280/radarr` and `http://<host>:30280/sonarr` with the API key and
quality profile `1`. Details and rollback: [Deployment](docs/DEPLOYMENT.md).

## Configuration

Environment variables (prefix `MAE_`, see `.env.example`) provide the initial values.
Most settings are then edited in the admin UI and saved to `runtime-config.json` in the
state directory; the worker applies changes within ten seconds. Secrets are never sent
back to the browser: an empty secret field keeps the stored value.

| Area | Settings |
|---|---|
| Quality | resolutions, fallback resolutions, codecs, size limits, source weights and denied sources, Russian audio required, voice and studio weights |
| Downloads | parallel downloads, stall time, minimum seeders and speed, longest allowed download, disk reserve, speed schedule |
| Upgrades and cleanup | upgrade check interval, automatic replacement, cleanup by age or ratio, release card cache |
| Sources | per source: protocol, URL, key, cookie, request interval, card selector, categories |
| DeepSeek | key and daily/per-request budgets (optional) |

Source configuration, including how to set up a Prowlarr gateway and which sources are
verified: [source matrix](docs/SOURCE_SUPPORT_MATRIX.md). Secrets (keys, cookies, source
URLs with passkeys) are kept out of logs, error messages and stored evidence.

## Command line

```sh
mae migrate     # apply database migrations
mae api         # admin UI and API on :8080
mae worker      # the worker loop
mae once        # a single worker pass
mae reconcile   # sync requests with Seerr once
python -m mae.backup backup <state-dir> <snapshot-dir>   # consistent SQLite snapshot
```

## Development

```sh
uv sync --frozen
uv run pytest -q                 # offline tests, ffmpeg is needed for the import tests
uv run ruff check mae migrations tests
```

`tests/live_*.py` are opt-in scripts for real Seerr, Jellyfin, qBittorrent and DeepSeek
instances and are not collected by `pytest`. Never commit `.env`, cookies, passkeys or
account data.

## License

AGPL-3.0-only, see [LICENSE](LICENSE). You may use, change and share aiarr freely, but a
modified version that you distribute or run as a network service must stay open under the
same license, keep the copyright notices and offer its source code to its users.

## Documentation

[Operations log](docs/OPERATIONS.ru.md) (RU) · [Deployment and rollback](docs/DEPLOYMENT.md) ·
[Acceptance record](docs/ACCEPTANCE.md) · [Seerr contract](docs/SEERR_CONTRACT.md) ·
[Source matrix](docs/SOURCE_SUPPORT_MATRIX.md) · [Adapter SDK](docs/ADAPTER_SDK.md) ·
[Architecture decision](docs/ADR-001-core.md) · [Reuse audit](docs/REUSE_PLAN.md) ·
[Third-party notices](THIRD_PARTY_NOTICES)
