# Acceptance checkpoint — 2026-09-09

## Seerr repair and live web administration — 2026-09-23

Seerr's request list calls `getProfiles()` on **every** configured Radarr and
Sonarr service, including non-default entries with sync disabled. After app
deletion, its historical ID 0 entries caused the user-visible connection
warning. With a prior Seerr ZFS snapshot
`data/ix-apps/app_mounts/seerr/config@pre-aiarr-legacy-repoint-20260923`, the
ID 0 entries were repointed to aiarr's compatible API and left non-default,
`syncEnabled=false`, `preventSearch=true`. Historical Seerr requests retain
their `serverId=0`. Two live request-list queries returned HTTP 200 and empty
Radarr/Sonarr `serviceErrors`; this was reconfirmed after deployment.

Image `media-acquisition-engine:dd5b275` is now deployed (TrueNAS update job
49979 `SUCCESS`; image ID
`sha256:5c513257e33244eecc716c748306832de9ac7184044414747df751d0a4eaa680`).
Source archive SHA-256:
`2dfcdaed580d92f6afc0acd54c711acd652557d8b651c9db3d389d4006faf3e7`.
The authenticated `/admin` page and `/admin/config` both returned HTTP 200;
the configuration response omitted API keys and credentials. The order detail
reported Silo S03E01–E10 available, S04E01 missing, and the S03E10 import
`PUBLISHED`. Its current state is `WAITING_FOR_RELEASE/NO_PROVEN_CANDIDATE`
after a later search for S04E01. The page now displays an order timeline,
search decisions, verified release facts, torrent progress and episode
coverage. It also edits source adapters (anonymous or API-key/Cookie based),
quality, integrations, DeepSeek credentials and queue limits.

A reversible live settings test changed `max_downloads` from 2 to 3 and back to
2 via the authenticated API. The worker logged two `runtime_config_reloaded`
events without restart; the worker's effective setting returned to 2. API
container is healthy; `/health` returned HTTP 200, `dry_run=false`. Private
app configuration and verified SQLite backup are at
`/mnt/data/apps/aiarr-webui-20260923`; snapshots
`data/apps/aiarr-state@pre-webui-20260923` and
`data/ix-apps/app_configs@pre-aiarr-webui-20260923` were taken before the
deployment. Offline validation: 238 tests passed, Ruff clean, admin JavaScript
syntax check passed.

## Production replacement completed — 2026-09-23

TrueNAS runs `media-acquisition-engine:59523c2` with `MAE_DRY_RUN=false`.
The image update job 49813 succeeded; authenticated API health returned HTTP
200, `status=OK`, `dry_run=false`. Radarr and Sonarr were stopped and deleted
through TrueNAS jobs 49819 and 49820, both `SUCCESS`. `app.query` reports both
absent; aiarr, Seerr, Jellyfin, qBittorrent, and Prowlarr report `RUNNING`.
Seerr's default movie and series services are aiarr (ID 1). Historical service
records (ID 0) remain for existing requests, with `syncEnabled=false` and
`preventSearch=true`.

A real NNM-Club Silo season-three torrent was added once to qBittorrent.
Only S03E10 had nonzero file priority; S03E01–E09 were skipped. The selected
5,234,521,556-byte H.264 file has a 1918×802 cropped picture and Russian
audio metadata. It passed the deployed media probe and was published as a
verified hardlink at
`/data/store/serials/Silo/Season 3/Silo - S03E10.mkv` (`nlink=2`). Jellyfin
reports S03E10 in coverage. The aiarr import journal records `PUBLISHED`, and
the series job is `WAITING_FOR_RELEASE/PARTIAL_SEASON` because S04E01 is still
missing. The approved Spider-Man: Brand New Day movie job is live but remains
`WAITING_FOR_RELEASE/NO_PROVEN_CANDIDATE`. Both jobs have `dry_run=0`.
qBittorrent retains the one complete aiarr torrent at progress 1.0 for seeding.

Before cutover, private app configuration and verified SQLite backups were
saved at `/mnt/data/apps/aiarr-cutover-20260923`; a further aiarr backup was
saved before the partial-pack download. ZFS snapshots include
`data/ix-apps/app_mounts/radarr/config@pre-aiarr-cutover-20260923`,
`data/ix-apps/app_mounts/sonarr/config@pre-aiarr-cutover-20260923`,
`data/ix-apps/app_mounts/seerr/config@pre-aiarr-cutover-20260923`,
`data/ix-apps/app_configs@pre-aiarr-cutover-20260923`, and
`data/apps/aiarr-state@pre-aiarr-cutover-20260923`. App deletion used
`remove_images=false` and `remove_ix_volumes=false`; retained data can support
a deliberate rollback. Offline validation for the deployed commit: 232 tests
passed and Ruff clean.

## Historical production replacement preflight — 2026-09-23

User authorized replacing and deleting Radarr/Sonarr. The cutover is not yet
safe: the two current approved production works have no policy-proven release.
Radarr and Sonarr remain running and remain Seerr's default services. No aiarr
torrent was added, and `MAE_DRY_RUN` remains true.

Before any app changes, TrueNAS ZFS snapshots were taken for Radarr, Sonarr,
Seerr, app configuration, and aiarr state. Private app configuration and
consistent SQLite backups (Radarr, Sonarr, Seerr, aiarr) are retained at
`/mnt/data/apps/aiarr-cutover-20260923`; each SQLite backup passed
`quick_check`, and the aiarr backup passed its manifest verification.

Commit `c6f2bb1` parses numbered Russian audio and AVC/HEVC codec evidence from
source cards, rejecting a title/card codec conflict. Offline: **213 passed**,
Ruff clean. Source archive SHA-256:
`dea06cf3e82979b96e66877382997cea399bbcd277e00210a26b318dd2d4217b`.
The TrueNAS app update job 49284 succeeded. Both aiarr containers run
`media-acquisition-engine:c6f2bb1`, image ID
`sha256:4451c35ff7862cee3c6b440da94d83cb3f676cce69d2e6038ed8124149c230cf`.
The API was healthy with `dry_run=true`; the Prowlarr BigFAN Torznab endpoint
passed its live capability check from the worker container. Configured providers
are now NNM-Club and BigFAN through Prowlarr.

A real retry of the approved Silo request examined 78 candidates (50 NNM-Club,
28 BigFAN), with no provider errors. The BigFAN card proved H.264 and Russian
audio, but the only relevant release contains S03E01–E10 while the library
already has S03E01–E09; the policy correctly returned `EPISODE_MISMATCH`.
S04E01 is also requested but not available in these source results. The approved
Spider-Man: Brand New Day retry examined three NNM-Club candidates and found
no proven release. Both jobs returned to `WAITING_FOR_RELEASE` with
`NO_PROVEN_CANDIDATE`. qBittorrent had one existing torrent and zero in the
aiarr category; all inventoried apps remained `RUNNING`.

Production cutover still needs a released, policy-proven acquisition and import,
plus a safe way to acquire only missing episodes from a season pack (or a
verified standalone S03E10 release). Keep the legacy services until that live
flow succeeds and Seerr defaults are switched deliberately.

## Episode unmonitoring — 2026-09-23

Offline: **211 passed**, Ruff clean. Removing an episode from a series request
now excludes it from future work. If the job already owns a torrent, removing
any episode pauses the torrent on the next worker tick and leaves the job in
`NEEDS_REVIEW` with its files intact. Removing the last episode before a download
waits without starting one; monitoring it again reopens the request. A completed
job with no monitored episodes no longer reports missing library media.

The image `media-acquisition-engine:unmonitor-20260923` was built on TrueNAS
from a source bundle with SHA-256
`e9e0c8fd2b689e15bbb375a59d867e6f340ef642bee4bdf20151b289ee60debb`.
Image ID: `sha256:8ebbb3ecc63581b01061d50e95de81da5cdeea118136609b7e2231f983c216b1`.
In the disposable Seerr 3.4.1/qBittorrent 5.2.3/Jellyfin 10.11.11 stack,
qBittorrent transfer/probe/hardlink and Jellyfin discovery passed. A real
selected-season Seerr request added a synthetic torrent; unmonitoring one of
its 13 episodes through the facade moved the job to `NEEDS_REVIEW`, retained
the torrent, and qBittorrent reported `stoppedDL`. No import was published.
The disposable containers and networks were removed after the test.

Before deployment, the app Compose configuration and consistent schema-0007
SQLite backup were saved in the private directory
`/mnt/data/apps/aiarr-pre-unmonitor-20260923-1426`. TrueNAS `app.update`
changed only the image tag for `api`, `worker`, and `migrate`. Post-update:
app `RUNNING`, API `healthy`, authenticated health `dry_run=true`, schema
`0007`, the same two dry-run jobs and zero active aiarr torrents. The live
Sonarr-compatible facade returned 11 monitored episodes. This verifies the
deployed runtime and an isolated real-service transition; no production
in-progress torrent was unmonitored, and no legacy Seerr ownership was changed.

## Non-TV work — 2026-09-21

Offline: **207 passed**, Ruff clean. Commit `999547f` adds migration 0007,
atomic persistent per-day/per-job-generation DeepSeek call/token budgets,
failure/latency metrics, strict malformed-response handling, and avoids LLM
calls for hard-rejected candidates. Per-request admission is concurrency-tested.
The partial-season regression runs real ffprobe/hardlinks with mocked external
services: first episode, restart, second-episode-only search/import, then a new
third episode requested without redownloading the first two. This is not a
live partial-season torrent certification.

Commit `d4591d6` adds non-overwriting SQLite backup/restore with WAL-safe backup,
checksums, integrity/foreign-key/schema checks and owner-only backup permissions.
Live rehearsal on a read-only-mounted production DB copy passed: schema 0006
backed up, restored and upgraded to 0007 with row counts preserved. Private
artifact directory: `/mnt/data/apps/aiarr-restore-rehearsal.t71fHY`.
This does not certify a complete production app/config/media rollback.

The first live E2E rerun passed qBittorrent transfer and Jellyfin discovery, but
timed out at Seerr readiness. Installed Seerr 3.4.1 code showed that default
`/api/v1/status` awaits GitHub update checks. Its documented query parameter
`checkUpdateAvailable=false` returned HTTP 200 immediately and is now used by
the test. Failed fixture directory: `/mnt/data/apps/aiarr-series-e2e-20260920.qSyY74`.
That disposable stack and the separate diagnostic Seerr were removed; test
files were retained. Rutor gateway search returned TEMPORARY_FAILURE, not PASS.

Production deployment and final E2E result are recorded separately below when
verified; a successful local build is not a deployed change.

Final series E2E rerun **passed** from `d4591d6`: real Seerr movie/selected-season
contract, synthetic 13-episode torrent, actual worker SIGKILL recovery, 13
verified hardlink imports and Jellyfin AVAILABLE without duplicate torrent or
import. Fixture directory: `/mnt/data/apps/aiarr-series-e2e-20260920.a3gijV`.
Disposable services/networks were removed automatically; synthetic files remain.

Runtime `media-acquisition-engine:d4591d6` was submitted via TrueNAS app.update
job 43018 after config/consistent SQLite backup in
`/mnt/data/apps/aiarr-pre-d4591d6-h6gygvnx`. Built digest:
`sha256:41d07375ee8e577d73c3ac62ac44e2ff5fde3772908a50715fdabd5dac17a68b`.
Post-update verification passed: both API/worker running the new tag, schema
0007, authenticated health and outcome metrics, dry-run true, and official
DeepSeek smoke PASS (one call, cache verified). No production download cutover.

## Resumed verification — 2026-09-20

### Latest continuation: DeepSeek and TV preparation

Deployed runtime is now `media-acquisition-engine:d442e18`, image digest
`sha256:0de6ee703cee332c2f6c655cbe798e03bbd05f6220f2ed5f842e92e1d3cc4d08`.
API and worker were verified running that tag. Config and consistent SQLite
backup: `/mnt/data/apps/aiarr-pre-d442e18-jp9b0ju4` (private).
The key supplied by the operator is installed; official DeepSeek smoke now
**passes** with one call and verified cache reuse. Initial live failures were
caused by missing enum instructions in the prompt and were fixed in `e3a2f40`.
The hard-rejected-candidate review fix is also now deployed. Dry-run stays true.

Full offline suite including television fixture safety tests: **186 passed**,
Ruff clean. Read-only Jellyfin session diagnostics are available with
`python -m mae.playback`; missing sessions or fields are not playback success.
Live inventory found Jellyfin 10.11.11, 58 existing items (SDR only), no active
sessions. Synthetic 4K generation exceeded the production worker's 1 GiB limit;
fixture generation was moved to a disposable 3 GiB / 2 CPU container. Existing
media was not modified. Television observation remains outstanding.

TV preparation subsequently passed: all four original synthetic SDR/HDR10
fixtures were fully decoded and discovered by exact path in the separate
`AIARR TV Tests` Jellyfin library. Generator returned `READY_FOR_TV`; temporary
3 GiB / 2 CPU container was removed automatically. See PLAYBACK_PROFILE.md for
the device checklist and limitations (no DV/surround/network certification).
This reaches the requested physical-TV test stage, not full project acceptance.

The entries below are earlier checkpoints, not current deployment/key status.

Current offline checkout: **171 passed**, Ruff clean (two upstream deprecation
warnings). Commit `3c60dc2` prevents unknown fields on already hard-rejected
candidates from unnecessarily triggering manual review; this runtime change is
not yet deployed in the image listed below.

Isolated series acquisition E2E passed: real Seerr selected-season request,
13 synthetic S02 episodes, one local multi-file torrent (34,413,613 bytes),
worker SIGKILL recovery, per-file verification, 13 hardlink imports, exact
episode coverage and Jellyfin availability. Repeated events produced no duplicate
torrent/import. Fixture directory:
`/mnt/data/apps/aiarr-series-e2e-20260920.x2PnxA`.
The disposable Compose stack was removed; fixture files remain. Run via
`tests/live_seerr.py --acquire-series-fixture` in the isolated full test stack.
Synthetic silence tagged `rus` proves metadata handling, not speech recognition.
This does not certify partial-season updates, new-episode acquisition or TV playback.

Read-only Rutor probe through the existing Prowlarr Torznab endpoint returned
`RATE_LIMITED`; no download was attempted and no source configuration changed.
This gateway is not yet live-certified. No repeated immediate retries were made
after observing this result.

Latest deployed runtime: `media-acquisition-engine:3eec8fe`, verified running
image `sha256:2d84fba41db76ad997910d6bae99217069378ccc972981b1efbff5cf3351553c`.
Rollback config/consistent SQLite backup:
`/mnt/data/apps/aiarr-pre-3eec8fe-cncm0vyv`. The runtime adds preservation of
nested-br technical evidence, exact NNM search sizes/seeders, explicit narration
language parsing, and a diagnostics dashboard at `/admin`. The public login
shell contains no operational data; API reads/actions still require the key.
The key is held only in page memory. HTTP auth tests and JS syntax checks passed;
a browser-driven UI test has not yet been performed.

The user approved the V4.1 Flash update: the sole model is now `deepseek-flash`;
the old API alias routes to a retired-model replacement and is rejected by
configuration validation. Cache namespace changed. Offline total at runtime
commit: **169 passed**; Ruff passed. Official live smoke is available as
`tests/live_deepseek.py --run-live`; without the still-missing key it reports
BLOCKED/exit 2, not PASS. See DEEPSEEK.md for verified official contract links.

The latest production dry-run movie job reached WAITING_FOR_RELEASE: its three
results have forbidden sources, with no unknown mandatory fields or source
errors. The series job had 50 candidates and no source errors. Media/download
mutations remain disabled. The completed movie test stack was shut down;
synthetic media remains in its isolated staging directory.

- Offline: 152 tests passed; Ruff passed. Commits `0076a81`, `2d63d4b`,
  `2b023ae` add per-source failure stages, retain topic query parameters, and
  parse NNMClub's current first-post markup without including replies.
- Live NNMClub root causes confirmed: passing `params={}` stripped `?t=...`
  and caused HTTP 404; current pages use `.postbody`, not `.post_body`.
  Four real cards from two requests now yield nonempty cleaned evidence.
  Both production dry-run jobs evaluated (50 / 3 candidates) with no source
  errors and entered NEEDS_REVIEW. Missing mandatory fields remain explicit;
  no production torrent was added.
- Isolated full movie E2E rerun passed: real Seerr movie and selected season
  (13 episodes), synthetic 2,647,201-byte local torrent, actual worker SIGKILL
  recovery, verification, one hardlink import, exact-ID Jellyfin discovery,
  AVAILABLE, no duplicate torrent/import. Series acquisition and TV playback
  are not certified by this movie fixture.
- Deployed image `media-acquisition-engine:2b023ae`; both running containers
  verified as `sha256:127a637309fccbf73e432988d8bd1bb2cc743e0d05c1f5de494c56a3204b2c94`.
  Dry-run remains enabled. Config and SQLite backup:
  `/mnt/data/apps/aiarr-pre-2b023ae-6j7d6t6l` (private root-owned directory).
- Production Seerr now has saved aiarr Radarr/Sonarr services, each ID 1,
  non-default, scan disabled, automatic search disabled. Existing services
  were compared before/after and remain unchanged. This is staged integration,
  not cutover. Seerr settings backup:
  `/mnt/data/apps/aiarr-seerr-before-facades-8ddimygs`.
- DeepSeek key is absent in the deployed configuration; live smoke test remains
  blocked on a key. All older observations below are historical checkpoints.

**Not accepted as a complete implementation of the specification. Do not switch
production Radarr/Sonarr yet.** This report separates code from tested behavior.

## Implemented

Python package, FastAPI facade and authenticated diagnostics; SQLAlchemy SQLite
jobs/events/attempts/import journal with Alembic migration; single worker OS lock;
Torznab and owned JSON protocol adapters; separate descriptions/download handles;
GuessIt parsing and conservative Russian-audio/studio rules; additive deterministic
score after constraints; restricted DeepSeek JSON client/cache/daily call budget;
qBittorrent ownership and timeout reconciliation; ffprobe verification, no-copy
hardlink publication; Jellyfin external-ID and episode coverage checks.

## Verified offline

`uv run pytest -q`: **112 passed**, no skipped tests, two dependency deprecation
warnings at this checkpoint. `uv run ruff check mae migrations tests` passes.

Seerr request lifecycle (migration 0004) is verified offline: explicit terminal
status cancels only in-flight acquisition; a missing API page does not cancel;
repeat/new approval opens a new generation; an engine-admin cancellation is not
reopened by the same request; parallel approved requests and available media are
retained. Live verification on the disposable Seerr 3.4.1/Jellyfin 10.11.11
stack also passed: deleting the old approved request then creating a new one
opened generation 2, with no production service or media path involved.

Cancellation policy (migration 0005) is verified offline: an in-progress owned
torrent is paused exactly once on the next worker tick while retained for review;
a qBittorrent failure leaves it pending for retry. A live qBittorrent pause check
passed on isolated qBittorrent v5.2.3: a synthetic owned torrent was downloaded,
paused, probed and hardlinked. No external tracker or production download path was
used.
Tests include authentication, approved-only facade writes, selected seasons,
event deduplication and restart, title/subtitle language false positives, wrong
identity/episode, studio matching, malformed/XXE XML, auth pages, rate limits,
SSRF/redirect rejection, invalid LLM schema/quotes, real local ffprobe, hardlink
collision/restart/EXDEV, foreign torrent ownership, and add-timeout no-replay
with the SDK's actual enum namespace.
Import fault injection now covers a crash after the hardlink exists but before
journal commit: restarting completes one import without a second link/download.
Probe regressions reject unsupported actual resolution and contradictions with
the selected release's declared resolution/codec.
New-episode reopening clears stale torrent intent/candidate while retaining import
history. All-provider temporary failures consume bounded attempts and respect
Retry-After (including a two-hour rate-limit regression). This retry change is
offline-tested; the live image below predates that isolated retry change.
Migration 0002 backfills stable episode IDs without inventing availability.
Offline tests cover multiple approved season requests, stable IDs across restart,
approved-only episode monitoring, partial season import, an invalid second file
preventing publication of an entire pack, and disappearance from Jellyfin.
Completed jobs are rechecked every five minutes; HTTP failures preserve earlier
observations, while a successful missing-media response requires review. A stale
observation cannot overwrite a newly extended request. Combined episode names
use contiguous ranges; gaps, duplicate numbers and reversed ranges are rejected.
Migration 0003 stores Servarr-compatible tags and their job associations. Tags are
validated on add/update, exposed by both facades, and cannot be deleted while a
job references them.

Upstream subsets: Faucet 106 passed; RTN 142; GuessIt API 30; Slimarr scoring 24.
Prowlarr v11 Rutor/Kinozal definitions validated with upstream `scripts/validate.py`.
qbittorrent-api upstream test collection requires a client even under `-m offline`;
that attempted run failed and is not included in the pass count.

## Verified live / real services

* TrueNAS 25.10.7, current containers/paths/dataset inspected afresh.
* Container image built successfully on NAS; dependencies installed inside image only.
* Two isolated qBittorrent **5.2.3** containers transferred an original synthetic
  **2,647,201-byte** media file. Byte comparison passed; ffprobe passed; hardlink
  source/destination shared inode/device with **link count 2**. Repeated add left
  one category `mae` torrent. Closed Docker network, no published ports, no
  production media mounts. This is NOT full Seerr→Jellyfin E2E and not speech recognition.
* Existing Prowlarr Torznab search for the openly distributed title Big Buck Bunny:
  BigFANGroup 4 results; Kinozal 1; NNM-Club 3; Rutor 5; RuTracker 10. Counts are
  observations, not claims of complete/correct results or download permission.
* Existing Seerr API read succeeded: six requests, four approved at observation.
* Isolated Jellyfin **10.11.11** discovered the synthetic hardlinked file using
  the shared engine naming function and exact provider ID. No external metadata
  fetchers enabled for this fixture library. This is not an LG playback test.
* Isolated Seerr **3.4.1** onboarded against that Jellyfin; both facade connection
  tests passed. Actual Seerr movie request and selected season 2 created exactly
  two engine jobs, with **13 requested episodes**, in dry-run, without torrents.
  Two repeated reconciliation events retained exactly the same job IDs, no duplicates.
  No Radarr/Sonarr services exist in the test network. Metadata requests use TMDB;
  the TV request is metadata-only, not a download.
* **Worker-driven synthetic movie E2E passed** using `live_seerr.py --acquire-fixture`:
  real Seerr request → real JSON protocol adapter/local fixture HTTP server →
  durable worker → qBittorrent swarm → ffprobe → hardlink → exact-ID Jellyfin
  discovery → AVAILABLE and facade hasFile=true. **2,647,201 bytes**, one import,
  one matching torrent, link count 2. Worker was actually SIGKILLed after torrent
  addition and restarted with the same SQLite state; no direct DB edits or manual
  import calls used to advance this job. Repeated reconciliation kept job IDs.
  Tested image digest: `sha256:496e47081e5c1fac6663af3646452614acc521f55eaefeb5031d2d6f0fe2ac1f`.
  The requested identity is Big Buck Bunny, but bytes are explicitly synthetic
  test patterns/silence with a rus stream tag, not the movie or Russian speech.
  This proves mechanics, not content authenticity, tracker coverage, series E2E,
  Seerr availability scanning, or LG playback.
  First attempt failed safely with no download due to an incorrect test TMDB ID
  (12690 was Appaloosa). Correct ID **10378** and a title assertion are now used;
  earlier facade-only success remains valid as an Appaloosa request contract test.
* After TMDB connectivity recovered, the live Seerr extension test passed: a
  request for season 3 extended the existing season-2 series job to **26**
  episodes. Existing episode IDs remained stable and approved monitoring passed.
  Worker was not started for this metadata/contract test.
* AlexFilm/Flarrow public catalog pages reachable through browsing; RHS fetch 403.
* Rutor public topic `384544` and its page-provided flat file-list endpoint were
  fetched from the NAS host. The concrete adapter and fixtures were implemented,
  but its isolated-container request ended `TEMPORARY_FAILURE`: NAS local DNS
  resolves the host to Fake-IP `198.18.0.144` while the container bridge has no
  route through the host's proxy. `trusted_source` is an explicit exact-origin
  option; it does not make this source reachable. No torrent payload was fetched.
* New real Jellyfin series-library test passed: a synthetic `S01E01-E02` file
  was observed as episodes 1 and 2; publishing episode 3 changed coverage to 1–3.
  Uses the shared naming/hardlink code, exact TVDB ID and real Jellyfin 10.11.11.
  This test directly publishes synthetic fixtures; it is not a series download E2E.
* New source-mounted API passed both real Seerr connection tests. The new
  `--extend-season` flow could not proceed: Seerr returned HTTP 500 with
  `[TMDB] Failed to fetch movie details: read ECONNRESET`. This is a failed live
  attempt, not a pass. Existing earlier movie/season and synthetic E2E passes
  remain historical results from their recorded checkpoints.
* Clean test image rebuilt successfully after network recovery:
  `sha256:3a02aca549ea7f08895bb5d9ec4732d7f4b3a6a8b16442ac2b269e93eb94a274`.
  A fresh isolated Seerr/Jellyfin stack then passed movie request, selected season,
  season 2→3 extension and 26 stable episode IDs with migrations 0001–0003.

Real swarm command (requires staging directory prepared with UID 3000 write access):

```
docker compose -f compose.test.yaml up -d seed leecher
docker compose -f compose.test.yaml run --rm test
docker compose -f compose.test.yaml down
```

## Access or observation required

DeepSeek API key for official smoke test; LostFilm/NewStudio gateway setup and any
interactive source login; approved private card fixtures or authenticated card
access; actual LG G5/webOS/Jellyfin playback observation and network measurements.
SSH access, TMDB connectivity and a clean test-image build were restored.
The production source network must offer a permitted container route or gateway to
Rutor before its adapter can be live-certified; host networking is not used as a
workaround.
Existing Prowlarr credentials remain on NAS and were never printed or copied into Git.

## Blocked by source / access method

RHS public page fetch returned 403. This does not establish that all permitted
access methods are blocked. No DRM/account restriction bypass attempted.

## Outstanding implementation / acceptance (not hidden as access blockers)

Real-source movie E2E and partial-season/new-episode acquisition E2E,
generation-aware re-request and cancellation reconciliation, source-specific card
parsers/fixtures/file-list/live downloads for all required sources, direct studio
catalog adapters, live source magnet coverage, complete evidence/audio-track models
and field-specific conflict resolution, manual evidence review UI, full device and
download profiles, original-language preference, more precise disk reservations,
bounded alternatives/blocklists, complete import crash/concurrency fault matrix,
300 independently labeled representative samples with >=100 independent holdout,
accuracy/abstention/latency report, production migration/backup/rollback exercise,
LG observation and subtitle-default adjustment. Image apt snapshot/SBOM and complete
license-text bundling also remain production release work.

Default dry-run retained. Production Seerr/Radarr/Sonarr/Jellyfin settings have
not been switched. Test-generated media is confined to the staging test directory.
The isolated service stack was shut down after testing; fixture media is retained.
