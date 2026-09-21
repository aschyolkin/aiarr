# Source support matrix — 2026-09-09

Protocol implementation is not live site certification. No source cookies or
private fixtures have been imported. No random mirrors selected.

2026-09-21 gateway recheck: public Rutor via existing Prowlarr Torznab returned
TEMPORARY_FAILURE. Earlier 2026-09-20 check returned RATE_LIMITED. Neither is
successful live certification; no torrent was requested, no tracker login or
gateway configuration change was performed.

2026-09-20 update for NNMClub: fixed topic-query loss (`params={}` in httpx)
and current `.postbody` markup. Four anonymous live cards passed through the
actual adapter in the production worker network. Production dry-run searches
evaluated 50 and 3 candidates without source errors. Size/audio completeness
still requires work; no torrent payload or source download was requested.
The older TEMPORARY_FAILURE observations below preceded these fixes.

| Source/address | Integration | Auth | Verified | Remaining limitation |
|---|---|---|---|---|
| RuTracker https://rutracker.org/ | Torznab + Jackett `RuTracker.cs`; optional separately configured card enricher | Account/captcha in gateway; card cookie separate | Upstream implementation audit, protocol fixtures | No account live search/download/card/file-list test |
| Rutor https://rutor.info/ | Direct anonymous `rutor` search → magnet + concrete card/files adapter; Torznab optional | Public | 2026-09-09 NAS-host search HTTP 200 / 5 results; topic `384544` and file list fetched; later anonymous read-only search endpoint check: HTTP 200 / 14,847 bytes; fixtures pass | Adapter accepts only its exact HTTPS origin. On the deployed `aiarr` Docker network its public HTTPS transport returned `ConnectTimeout`, so it remains disabled there pending an explicitly tested source route/gateway. No torrent/magnet was downloaded |
| Kinozal https://kinozal.tv/ | Torznab + `kinozal.yml` | Account | Login/settings implementation inspected | Disable `addrussian`; card auth/live download not tested |
| NNM-Club https://nnmclub.to/ | Concrete anonymous `nnmclub` search/card/download-handle adapter; Torznab remains optional | Anonymous search/card where site permits | 2026-09-09: live search `Big Buck Bunny` returned 5 results; later anonymous read-only endpoint check: HTTP 200 / 96,276 bytes; deployed `aiarr` worker transport HTTP 200 / 156,526 bytes and real adapter search returned 5 candidates; Windows-1251 parser fixture passes | Adapter accepts only `https://nnmclub.to`; card request returned `TEMPORARY_FAILURE` twice from this network; no `download.php` request was made. Live card/download still outstanding |
| LostFilm https://www.lostfilm.tv/ | `LostFilmCatalog` read-only public series catalog and `/search/`; Torznab optional | Login/captcha for account-specific operations | 2026-09-09 public home HTTP 200 (23 cards); public search returned «Во все тяжкие / Breaking Bad»; fixtures pass | Numbering/quality/download are not implemented or verified; catalog is not a download provider |
| NewStudio https://newstudio.tv/ | Torznab + `newstudio.yml` | Definition public; IP restrictions | Search/download selectors inspected | TLS connection timed out twice on this network; upstream IP-filtering warning remains; no live claim |
| AlexFilm https://alexfilm.tv/ | `studio_matching_only` + read-only `AlexFilmCatalog` | Site offers Google/Yandex login | Public catalog fetched 2026-09-09; saved live page parsed into 9 entries; fixtures pass | No download interface verified; catalog is never a download provider |
| Red Head Sound https://redheadsound.studio/ | `studio_matching_only` + read-only `RedHeadSoundCatalog` | Login UI present | 2026-09-09 public home HTTP 200; saved live page parsed into 101 entries; fixtures pass | No torrent/download interface verified; catalog is never a download provider |
| Flarrow Films https://fla-fi.studio/ | `studio_matching_only` + read-only `FlarrowCatalog` | Login form present | Public catalog fetched 2026-09-09; saved live page parsed into 10 entries; fixtures pass | Older official posts mention torrents, so absence must NOT be asserted; current download/card integration not tested; catalog is never a download provider |

All five studio names recognized only in audio-scoped evidence, with equal default
weights. This does not prove the actual audio performance or direct studio download.
Configurable HTML enrichment is infrastructure, not six complete tested site parsers.
`fetch_file_list` explicitly returns `UNSUPPORTED_CAPABILITY` for Torznab except the
concrete Rutor card adapter, whose flat-file layout is checked before returning paths.
Magnet handles use a qBittorrent metadata-only preflight: only v1 `btih` magnets
are accepted, the client stops at `MetadataReceived`, and the engine validates the
reported size and file paths before it resumes the owned torrent. Missing metadata
within the bounded wait becomes `MAGNET_METADATA_UNAVAILABLE`/review; v2 or hybrid
magnets are explicitly unsupported. Live source magnet testing remains outstanding.

Evidence links: [AlexFilm](https://alexfilm.tv/),
[RHS](https://redheadsound.studio/), [Flarrow catalog](https://fla-fi.studio/),
[official Flarrow channel](https://t.me/s/flarrowfilmsofficial).
