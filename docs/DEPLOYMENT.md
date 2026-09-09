# Deployment and rollback

Experimental build. Read ACCEPTANCE.md before any production switch.
TrueNAS detected 25.10.7; its [Custom App documentation](https://www.truenas.com/docs/scale/25.10/scaleuireference/apps/installcustomappscreens/)
supports Compose YAML. Do not install Python packages in the TrueNAS base OS.

## Recorded isolated deployment — 2026-09-09

TrueNAS 25.10.7 was inventoried before deployment. `/mnt/data/store` is the
single media dataset and has the required `downloads/films`,
`downloads/serials`, `films`, and `serials` paths. A pre-change snapshot
`data/apps@pre-mae-20260909` was created, followed by the dedicated state
dataset `/mnt/data/apps/aiarr-state` (UID/GID 3000, mode `0700`). The original
`mae-state` was snapshotted again before the rename and is retained only for
rollback.

The `aiarr` TrueNAS Custom App is installed from local image
`media-acquisition-engine:0.1.0`, with `MAE_DRY_RUN=true`, the anonymous
`nnmclub` provider, and no Seerr/qBittorrent/Jellyfin credentials. Its API is bound only to
`10.110.111.2:30280`; the API mounts media read-only. Internal authenticated
health returned HTTP 200 and Docker reported `healthy`. A request from the
Seerr container reached the API and received the expected unauthenticated HTTP
401, proving the container-to-NAS route without exposing an API key. No new
files appeared under the download directories during this check.
An in-container UID 3000 probe created and removed two temporary files under
`downloads/films` and `films`; equal `st_dev`/inode and `nlink=2` confirmed
that this actual dataset boundary supports hardlinks.
The App was subsequently stopped and started through the TrueNAS lifecycle;
it returned to `RUNNING`/`healthy` with the NNM provider configuration intact.

Radarr and Sonarr were deliberately left running and Seerr was not reconfigured,
so no second automation owns a request or download. Use
`deploy/truenas-custom-app.compose.yaml.example` as the secret-free Custom App
template. It intentionally starts with no providers; Rutor remains disabled in
the deployed App pending a tested Docker-network source route. Do not add
integration credentials or disable dry-run until a separate cutover step has
been accepted.

Local development:

```
uv sync --frozen
uv run pytest -q
# Configure MAE_API_KEY, MAE_STATE_DIR and service secrets in .env first
uv run mae migrate
uv run mae api
# separate terminal
uv run mae worker
```

Required media tree under the ONE dataset, mounted as `/data/store`:
`downloads/films`, `downloads/serials`, `films`, `serials`.
Before production: inventory current images/configurations, back up Seerr through
supported filesystem/SQLite backup while stopped, snapshot relevant app datasets,
record old service settings and versions. Do not print secret-bearing config.
Create a dedicated persistent state directory with appropriate ACL for actual UID.
Test write access and hardlinks using generated files, never existing media.

Compose is a build/run definition for staging. For TrueNAS Custom Apps, prebuild
and publish a pinned image to an operator-owned registry, remove `build`, replace
image tag with tested digest, and supply env values/volumes via Custom Apps.
The base image/uv tags are versioned, but apt packages are not snapshot-pinned yet:
bit-for-bit container reproducibility is an outstanding acceptance item.

API default binds only host loopback. Seerr containers cannot reach that loopback:
configure a shared internal Docker network for the two applications or explicitly
bind to the NAS LAN address with network access controls before live onboarding.
Do not expose service ports to the Internet. API requires key, including health.
Worker and API share a local SQLite state directory; not NFS/SMB.

```
docker compose build
docker compose up -d
docker compose logs --tail 50 api worker
```

Run dry-run first. Do not disable dry-run until approved-only Seerr flow, provider
access, file verification, LG playback and recovery tests pass. Do not let old and
new automation own the same requests. Stop Radarr/Sonarr only at successful
cutover; retain their app datasets, config and credentials for rollback.

## Public source adapters

The direct `rutor` and `nnmclub` adapters are optional and use no account
credentials. Copy their commented `MAE_PROVIDERS` example from `.env.example`
only after a dry-run check. `rutor` is pinned to the configured HTTPS
`source_host`; `nnmclub` is pinned to `https://nnmclub.to`. A network mirror or
proxy is not selected automatically. When this TrueNAS intentionally resolves an
external source through a local Fake-IP proxy, `trusted_source: true` is a
per-source, explicit exception after the exact route is tested; it must never be
used as a general SSRF bypass.

Do not add source cookies, passkeys, or site passwords to `.env` for these two
anonymous adapters. A live catalog/search success is not permission to download:
leave `MAE_DRY_RUN=true` until the source, qBittorrent and import policy are
separately accepted.

Rollback: stop engine API/worker, restore the previous Seerr service configuration,
restart former Radarr/Sonarr. Retain category `mae` torrents for explicit review;
do not reassign/delete them automatically. Engine imports are hardlinks and must
not be edited in place. Restore stopped-engine state backup with matching image
if rolling back migrations. Never delete the media dataset.

No Bazarr, subtitle fetching, torrent streaming, debrid or player added.
Automatic subtitle display is a Jellyfin user/device preference and remains an
explicit unperformed live configuration check.

Schema 0002 introduces persistent episode IDs and cached Jellyfin availability.
Run `mae migrate` before starting the new API/worker. Existing episode coverage
starts unconfirmed; the worker rechecks completed jobs against Jellyfin at startup
and every five minutes. A successful observation of missing media moves the job
to review; failed HTTP observations retain its prior availability. Existing SQLite
backups and the matching older image are required for rollback.

Schema 0003 adds `tags` and `job_tags`. It is forward-only like the earlier
migrations. The API accepts only existing positive tag IDs when a Seerr add/update
payload supplies `tags`; unknown IDs cause HTTP 422. Used tags cannot be deleted.

Schema 0004 adds the durable Seerr-request journal and generation counter. It is
also forward-only: stop the app and take the same SQLite backup before upgrading.

Schema 0005 records safe qBittorrent pausing after cancellation. A cancelled MAE
torrent remains retained for explicit review and tracker policy, but the worker
pauses it on the next tick after verifying category ownership. A temporary client
failure remains pending and is retried; torrents outside category `mae` are never
paused by the engine.

Schema 0006 adds persistent per-episode `monitored` state. It separates Seerr
approval from user-desired coverage: unmonitoring does not delete an imported
file, while enabling an approved episode reopens an available series job.

## Isolated real-service tests

Prepare `e2e-data/seed` and `e2e-data/leecher` writable by UID 3000 on the staging
host, build `media-acquisition-engine:0.1.0-test` from this Dockerfile, then:

```
docker compose -f compose.test.yaml --profile full up -d seed leecher jellyfin seerr
docker compose -f compose.test.yaml run --rm test
docker compose -f compose.test.yaml --profile full run --rm --entrypoint python test /tests/live_jellyfin.py
docker compose -f compose.test.yaml --profile full run --rm --entrypoint python test /tests/live_seerr.py
# Optional: disposable Seerr request deletion + re-request lifecycle check
docker compose -f compose.test.yaml --profile full run --rm --entrypoint python test /tests/live_seerr.py --lifecycle
docker compose -f compose.test.yaml --profile full down
```

Only test Seerr has Internet egress for metadata; no ports are published and no
production paths are mounted. Seerr/Jellyfin configurations use disposable tmpfs.
The Seerr test starts its own API subprocess, fetches the test Seerr key into
memory, and never starts a worker. A rerun requires a fresh Seerr request store:
`docker compose -f compose.test.yaml --profile full up -d --force-recreate seerr`.
Wait for server startup before invoking tests. Fixture media remains in e2e-data
after shutdown. These separate legs do not certify full end-to-end acquisition.
The test service mounts current `mae` and `migrations` read-only over the runtime
image. This permits source integration tests with a cached image when registries
are unavailable, provided locked dependencies have not changed. It does not replace
a clean release image build. `--extend-season` on `live_seerr.py` exercises a new
Seerr request for season 3 after season 2, checking stable episode IDs and one series
job. Use a fresh Seerr config for each invocation.

Combined episodes are named with a contiguous range such as `S02E01-E02`, as
specified in [Jellyfin's show naming documentation](https://jellyfin.org/docs/general/server/media/shows/).
Noncontiguous, reversed, duplicate, or cross-season combinations require review.
After the Jellyfin movie setup, `live_series_library.py` can be invoked using the
same test container command. It checks real discovery of a combined file and a
later single episode without TMDB access. Use a fresh synthetic series directory
and test Shows library; it intentionally refuses to overwrite existing fixtures.

For the worker-driven synthetic movie E2E, use a fresh staging media directory
and fresh service configs. Run the qBittorrent and Jellyfin setup tests above,
then replace the final `live_seerr.py` invocation with:

```
docker compose -f compose.test.yaml --profile full run --rm --entrypoint python test /tests/live_seerr.py --acquire-fixture
```

This opt-in turns dry-run off **only inside the temporary test API/worker** and
serves a locally generated torrent through the real JSON adapter. No production
source is configured. The harness SIGKILLs and restarts the worker after add,
then waits up to 240 seconds for actual Jellyfin discovery. Test identity comes
from [TMDB Big Buck Bunny](https://www.themoviedb.org/movie/10378-big-buck-bunny),
but content is synthetic test patterns/silence, never represented as the real film.
Do not reuse its fixture source against production services. The series request
tests selection only; the fixture has no series release. Shut down the stack
afterward; retain fixture media rather than overwriting hardlinked files on reruns.
