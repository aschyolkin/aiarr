# Acceptance checkpoint — 2026-09-09

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

`uv run pytest -q`: **82 passed**, no skipped tests, two dependency deprecation
warnings at this checkpoint. `uv run ruff check mae migrations tests` passes.
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
* AlexFilm/Flarrow public catalog pages reachable through browsing; RHS fetch 403.
* New real Jellyfin series-library test passed: a synthetic `S01E01-E02` file
  was observed as episodes 1 and 2; publishing episode 3 changed coverage to 1–3.
  Uses the shared naming/hardlink code, exact TVDB ID and real Jellyfin 10.11.11.
  This test directly publishes synthetic fixtures; it is not a series download E2E.
* New source-mounted API passed both real Seerr connection tests. The new
  `--extend-season` flow could not proceed: Seerr returned HTTP 500 with
  `[TMDB] Failed to fetch movie details: read ECONNRESET`. This is a failed live
  attempt, not a pass. Existing earlier movie/season and synthetic E2E passes
  remain historical results from their recorded checkpoints.
* Latest clean container build failed while contacting `auth.docker.io`
  (`connection reset by peer`). New live tests used the existing runtime image
  plus read-only mounts of current application/migration source; dependencies
  were unchanged. A clean release image rebuild is still required.

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
Outbound NAS access to TMDB and Docker Hub must work before completing the latest
Seerr season-extension live test and clean container build. SSH access was restored.
Existing Prowlarr credentials remain on NAS and were never printed or copied into Git.

## Blocked by source / access method

RHS public page fetch returned 403. This does not establish that all permitted
access methods are blocked. No DRM/account restriction bypass attempted.

## Outstanding implementation / acceptance (not hidden as access blockers)

Real-source movie E2E and series acquisition E2E, live season-extension contract,
individual episode unmonitoring and tag contract,
generation-aware re-request and cancellation reconciliation, source-specific card
parsers/fixtures/file-list/live downloads for all required sources, direct studio
catalog adapters, magnet metadata preflight, complete evidence/audio-track models
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
