# Seerr facade contract

Inspected develop SHA in REUSE_PLAN and fetched installed tag **v3.4.1**,
SHA `69f73a6f1486fdb51b8ddae9a94a8dfb629f461c`.
Paths below derive from `server/api/servarr/base.ts`, `radarr.ts`, `sonarr.ts`.
Two URL bases: `/radarr` and `/sonarr`; Seerr appends `/api/v3`.
Seerr passes `apikey` query parameter; `X-Api-Key` also supported.
Access logging disabled to avoid query-key logging.

| Seerr method | Endpoint | Engine behavior | Test / status |
|---|---|---|---|
| getSystemStatus | GET system/status | Actual engine version and facade urlBase; no fake Radarr version | Live Seerr connection test passed |
| getProfiles | GET qualityProfile | Actual configured policy ID/version | `test_movie_and_selected_season` |
| getRootFolders | GET rootfolder | Canonical path, actual disk free/total | Implemented; requires mounted path |
| getTags/createTag/updateTag/deleteTag | GET/POST/PUT/DELETE tag | Persistent labels; job tag IDs validated; used tag cannot be deleted | `test_servarr_tags_are_persistent_and_validated` |
| getMovieByTmdbId | GET movie/lookup?term=tmdb:N | Approved Seerr metadata only | `test_movie_and_selected_season` |
| addMovie | POST movie | Approval rechecked, durable idempotent job | Same test |
| getMovies/getMovie | GET movie[/N] | DB job and imported availability | Implemented |
| getSeriesByTvdbId | GET series/lookup?term=tvdb:N | Metadata from approved Seerr request | Implemented |
| addSeries | POST/PUT series | Only approved selected seasons | Same test |
| searchMovie/searchSeries | POST command | Schedules actual job | Same test |
| getQueue | GET queue | Persisted active downloads/imports | Implemented, progress fields incomplete |
| deleteMovie/deleteSeries | DELETE movie/N or series/N | Cancel; refuses deleteFiles=true | `test_cancel_no_delete` |
| getLanguageProfiles | GET languageprofile | Russian profile | Implemented |
| getEpisodes/monitorEpisodes | GET episode / PUT episode/monitor | Persistent episode IDs; actual desired monitoring; recheck approval for monitored=true | Offline and real Seerr extension passed: seasons 2→3, 26 episodes |

Webhook POST `/events/seerr` re-fetches the request list; it does not trust event
fields as authorization. Worker polls every 300 seconds and deduplicates external
identity. Migration 0004 persists the request ID, status, active/suppressed state
and a job generation. An explicit terminal status cancels only in-flight work when
no other approved request remains; a missing API row never implies cancellation.
A later real approval opens a fresh generation and clears only its torrent intent.
An engine-admin cancellation suppresses current request IDs, so an unchanged,
still-approved request cannot silently restart it. `hasFile` is true only after
verification/import and Jellyfin visibility.
Partial season counts use individually observed Jellyfin episode coverage.
Episode IDs are persisted by migration 0002 and survive request merges/restarts.
Several approved requests for different seasons of one series are combined.
All desired episodes are monitored by definition; setting `monitored=true` is
idempotent and requires current Seerr approval. Disabling individual episodes
returns 422. Cancelled requests return 409 until Seerr re-approves or creates a
new request. These unsupported operations are not silently acknowledged.

Live `tests/live_seerr.py`: Seerr 3.4.1 onboarding via Jellyfin 10.11.11, both
connection tests, movie POST and selected-season POST passed; 2 dry-run jobs,
13 episodes only in selected season 2. No Radarr/Sonarr in the test network.
The opt-in `--acquire-fixture` variant additionally completes a synthetic movie
through the actual worker and checks facade `hasFile=true` after Jellyfin discovery.
It does not certify Seerr's own periodic availability scan or series acquisition.
`--extend-season` was later run successfully after TMDB connectivity recovered:
the real Seerr instance added season 3 to the existing selected season 2, retained
existing episode IDs, and accepted approved episode monitoring. It starts no worker.
Setup requires Jellyfin `urlBase: ""`, `serverType: 2`; Sonarr settings require
`enableSeasonFolders`. `MainSettings.apiKey` is read-only: fetch it through the
authenticated API and pass it to the temporary engine process in memory.

`--lifecycle` is an opt-in disposable-stack test for the Seerr operation that is
available to an administrator: delete an approved request, then create a new one.
It verifies the engine's admin cancellation remains cancelled across the missing
old request and the new Seerr request opens generation 2. It never targets a
production Seerr instance. It passed live on the isolated Seerr 3.4.1/Jellyfin
10.11.11 stack (generation 2 observed).

Limitations: individual episode unmonitoring and full metadata lookup contract are
not accepted yet. Cancellation/re-request has offline coverage but still requires
a live Seerr cancellation/re-request observation.
Offline facade tests use a controlled double; the separate live test uses real Seerr.
Production must not be switched based on these tests alone.
