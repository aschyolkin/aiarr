# Deployment and rollback

Experimental build. Read ACCEPTANCE.md before any production switch.
TrueNAS detected 25.10.7; its [Custom App documentation](https://www.truenas.com/docs/scale/25.10/scaleuireference/apps/installcustomappscreens/)
supports Compose YAML. Do not install Python packages in the TrueNAS base OS.

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

Rollback: stop engine API/worker, restore the previous Seerr service configuration,
restart former Radarr/Sonarr. Retain category `mae` torrents for explicit review;
do not reassign/delete them automatically. Engine imports are hardlinks and must
not be edited in place. Restore stopped-engine state backup with matching image
if rolling back migrations. Never delete the media dataset.

No Bazarr, subtitle fetching, torrent streaming, debrid or player added.
Automatic subtitle display is a Jellyfin user/device preference and remains an
explicit unperformed live configuration check.

## Isolated real-service tests

Prepare `e2e-data/seed` and `e2e-data/leecher` writable by UID 3000 on the staging
host, build `media-acquisition-engine:0.1.0-test` from this Dockerfile, then:

```
docker compose -f compose.test.yaml --profile full up -d seed leecher jellyfin seerr
docker compose -f compose.test.yaml run --rm test
docker compose -f compose.test.yaml --profile full run --rm --entrypoint python test /tests/live_jellyfin.py
docker compose -f compose.test.yaml --profile full run --rm --entrypoint python test /tests/live_seerr.py
docker compose -f compose.test.yaml --profile full down
```

Only test Seerr has Internet egress for metadata; no ports are published and no
production paths are mounted. Seerr/Jellyfin configurations use disposable tmpfs.
The Seerr test starts its own API subprocess, fetches the test Seerr key into
memory, and never starts a worker. A rerun requires a fresh Seerr request store:
`docker compose -f compose.test.yaml --profile full up -d --force-recreate seerr`.
Wait for server startup before invoking tests. Fixture media remains in e2e-data
after shutdown. These separate legs do not certify full end-to-end acquisition.
