# Deployment and rollback

Pending local fix (2026-09-24): schema 0008 retains every job's torrent hash
after partial-season import clears its active download. Confirmed Seerr deletion
cleans all recorded torrents; legacy hashes are recovered from exact imported
source paths in owned qBittorrent torrents and saved before deleting imports.
Schema 0009: each job starts its best candidate and the runner-up together; the
backup (`jobs.backup_hash`) is limited to `backup_download_limit` (100 KiB/s).
After `stall_seconds` (180): primary received data → backup deleted; only backup
received data → it is unlimited and becomes the job's torrent (`BACKUP_PROMOTED`);
neither → both releases are recorded in `stalled_releases`, deleted, and the next
two are selected (`DOWNLOAD_STALLED_NO_PEERS`). Deletions go through the
`discard_downloads` queue so an interrupted worker retries them. When every
usable release has stalled the list is cleared and retried an hour later.
qBittorrent queue limits raised on 2026-09-24 for two torrents per job:
`max_active_downloads` 3→6, `max_active_torrents` 5→10.
Back up SQLite before upgrading and run migrations before starting API/worker.
These changes have not yet been deployed.

Read ACCEPTANCE.md for the production acceptance record and rollback data.

Latest verified checkpoint (2026-09-23): aiarr runs
`media-acquisition-engine:aaeb441` with `MAE_DRY_RUN=false` and NNM-Club plus
BigFAN providers. Seerr defaults route movies and series to aiarr. A real
Silo S03E10 was downloaded selectively, hardlinked into the existing series
folder, and recognized by Jellyfin. Radarr and Sonarr apps were deleted after
this check. Their historical Seerr entries point to aiarr so the request list
does not warn about missing services. The web UI now shows order progress and
edits operational settings. Private backups and ZFS snapshots remain
available; see the 2026-09-23 sections in ACCEPTANCE.md. The historical
deployment sections below describe earlier staged states.
Toy Story 5 job 3 is downloading after the NNM-Club encoding repair; its
availability has not yet been confirmed by Jellyfin.

## Незапущенный пакет улучшений — 2026-09-23

После текущего образа `aaeb441` в рабочей копии подготовлены изменения для
следующего обновления: Seerr SSO для веб-интерфейса, сверка фактически удалённых
Seerr-заявок и серверная модель удаления принадлежащих aiarr файлов/торрентов.
Они ещё не считаются production-развёрнутыми: перед обновлением нужно завершить
идемпотентную очистку, прогнать полный набор тестов, создать backup SQLite и
конфигурации TrueNAS, затем проверить сохранение активных загрузок.

Правило удаления: отсутствие строки в пагинированном списке Seerr само по себе
не является основанием для удаления. aiarr сначала делает прямой запрос
`GET /api/v1/request/{id}` и продолжает очистку только при `404`. Файл удаляется
только если журнал импорта подтверждает путь внутри `/data/store`, обычный файл,
совпадение `st_dev/st_ino`, а torrent принадлежит категории `mae` и имеет тег
`media-acquisition-engine`. При любой проверке, которая не проходит, данные
остаются на месте и операция повторяется после исправления причины.

Administrative login: `http://10.110.111.2:30280/admin`. In the pending UI
update, first sign in to Seerr as an administrator; the browser's existing
Seerr session authenticates aiarr without asking for an API key on every page
refresh. The current deployed `aaeb441` interface still accepts the aiarr API
key as its fallback login. The dashboard shows queue, readable order history,
live torrent progress, episode coverage, evidence and metrics, with
authenticated retry/cancel actions. Open **Настройки aiarr** to add/remove
sources, update API keys or Cookies, quality, DeepSeek key and queue limits.
Blank secret fields preserve their current values. Settings are saved in the
private state dataset and loaded by the worker within ten seconds. The API key,
media paths and dry-run switch remain in the TrueNAS app configuration.
**Кнопка Web UI в Application Info.** TrueNAS 25.10 не строит portals для
custom-приложений (`crud.py`: только при `custom_app is False`). В compose aiarr
есть `x-portals` (Web UI → `http://10.110.111.2:30280/admin`); после
установки/переустановки приложения один раз заполнить metadata тем же кодом
TrueNAS (бэкап `metadata.yaml` рядом):

```
sudo python3 -c "from middlewared.plugins.apps.ix_apps.metadata import update_app_metadata_for_portals as f; f('aiarr', '1.0.0')"
sudo midclt call -j app.metadata.generate
```

Обычный `app.update` (смена тега образа) и stop/start кнопку не сбрасывают
(проверено 2026-09-24).

TrueNAS detected 25.10.7; its [Custom App documentation](https://www.truenas.com/docs/scale/25.10/scaleuireference/apps/installcustomappscreens/)
supports Compose YAML. Do not install Python packages in the TrueNAS base OS.

## Recorded isolated deployment — 2026-09-09

Backup utility (included in the image above):

```
uv run python -m mae.backup backup /path/to/state /path/to/new-backup
uv run python -m mae.backup restore-new /path/to/new-backup /path/to/new-restore
```

The destination must not exist. SQLite backup includes committed WAL data;
checksum, quick_check, foreign keys and schema are verified. Restoring never
overwrites the current state directory. This covers the engine database only:
back up private app configuration separately and retain media/download datasets.
For actual rollback stop the engine, restore to a new directory, validate with
the matching old image and explicitly switch its state mount. No automatic
destructive schema downgrade is provided. Local backup/restore fault tests do
not constitute a production rollback rehearsal. Migration 0007 introduces
DeepSeek budgets and metrics; back up state before deploying it.

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
The worker is configured with the NAS LAN URLs for Seerr (`30357`),
qBittorrent (`30024`), and Jellyfin (`30013`). Each returned HTTP 200 from the
worker namespace through its public status endpoint.
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

## Recorded service-credential and Seerr dry-run check — 2026-09-09

A point-in-time snapshot `data/ix-apps@pre-aiarr-service-keys-20260909` was
created before the Custom App update. Existing Seerr and Jellyfin service keys
were read locally from their persistent application configuration and inserted
only into aiarr's Custom App environment; no key was copied into this repository
or emitted to logs. qBittorrent's already enabled blank-auth service route was
verified through the installed Python client. From the aiarr worker, authenticated
read-only Seerr request listing, Jellyfin system-info, and qBittorrent version
calls all returned successfully.

An authenticated POST to aiarr's `/events/seerr` performed a real reconciliation
of the current Seerr data: three approved request records converged into two
durable aiarr jobs. Both jobs retained `dry_run=true`; no torrent hash was
created. This proves Seerr-to-aiarr request ingestion without reconfiguring
Seerr's Radarr/Sonarr services, adding a download, importing media, or changing
legacy automation ownership. The next cutover step remains an explicit Seerr
facade configuration and a carefully scoped request-routing decision.

After this ingestion check, Seerr's official non-persistent connection-test
endpoints validated both aiarr facades: `/api/v1/settings/radarr/test` and
`/api/v1/settings/sonarr/test` each returned HTTP 200 with the configured
quality profile. The test payload was constructed locally from the existing
aiarr API key and was not saved as a Seerr service. No default service, request
route, Radarr/Sonarr setting, or Seerr record was changed.

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
# Optional: isolated Seerr episode unmonitor -> real qBittorrent pause
docker compose -f compose.test.yaml --profile full run --rm --entrypoint python test /tests/live_seerr.py --unmonitor-series-fixture
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
