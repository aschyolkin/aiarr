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
| getTags | GET tag | Empty configured tag set | Implemented; tag creation not supported |
| getMovieByTmdbId | GET movie/lookup?term=tmdb:N | Approved Seerr metadata only | `test_movie_and_selected_season` |
| addMovie | POST movie | Approval rechecked, durable idempotent job | Same test |
| getMovies/getMovie | GET movie[/N] | DB job and imported availability | Implemented |
| getSeriesByTvdbId | GET series/lookup?term=tvdb:N | Metadata from approved Seerr request | Implemented |
| addSeries | POST/PUT series | Only approved selected seasons | Same test |
| searchMovie/searchSeries | POST command | Schedules actual job | Same test |
| getQueue | GET queue | Persisted active downloads/imports | Implemented, progress fields incomplete |
| deleteMovie/deleteSeries | DELETE movie/N or series/N | Cancel; refuses deleteFiles=true | `test_cancel_no_delete` |
| getLanguageProfiles | GET languageprofile | Russian profile | Implemented |
| getEpisodes/monitorEpisodes | GET episode / PUT episode/monitor | **Outstanding** | Not silently acknowledged |
| createTag/updateTag | POST tag / PUT tag/N | **Outstanding** | HTTP 404/405 |

Webhook POST `/events/seerr` re-fetches approved requests; does not trust event
fields as authorization. Worker polls every 300 seconds and deduplicates external
identity. `hasFile` is true only after verification/import and Jellyfin visibility.
Partial season counts use imported episode keys, not pack filename claims.

Live `tests/live_seerr.py`: Seerr 3.4.1 onboarding via Jellyfin 10.11.11, both
connection tests, movie POST and selected-season POST passed; 2 dry-run jobs,
13 episodes only in selected season 2. No Radarr/Sonarr in the test network.
The opt-in `--acquire-fixture` variant additionally completes a synthetic movie
through the actual worker and checks facade `hasFile=true` after Jellyfin discovery.
It does not certify Seerr's own periodic availability scan or series acquisition.
Setup requires Jellyfin `urlBase: ""`, `serverType: 2`; Sonarr settings require
`enableSeasonFolders`. `MainSettings.apiKey` is read-only: fetch it through the
authenticated API and pass it to the temporary engine process in memory.

Limitations: completion/cancellation propagation, generation-aware
re-request, episode monitoring and full metadata lookup contract are not accepted yet.
Offline facade tests use a controlled double; the separate live test uses real Seerr.
Production must not be switched based on these tests alone.
