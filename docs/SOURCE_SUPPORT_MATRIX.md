# Source support matrix — 2026-09-08

Protocol implementation is not live site certification. No source cookies or
private fixtures have been imported. No random mirrors selected.

| Source/address | Integration | Auth | Verified | Remaining limitation |
|---|---|---|---|---|
| RuTracker https://rutracker.org/ | Torznab + Jackett `RuTracker.cs`; optional separately configured card enricher | Account/captcha in gateway; card cookie separate | Upstream implementation audit, protocol fixtures | No account live search/download/card/file-list test |
| Rutor https://rutor.info/ | Torznab + `rutor.yml`; concrete `rutor` HTML card/files adapter | Usually public | Public topic `384544` and `/descriptions/384544.files` fetched from NAS host; adapter fixtures pass | In Docker bridge, local DNS returns Fake-IP and source request is `TEMPORARY_FAILURE`; requires an explicitly configured source route/gateway. No torrent download attempted |
| Kinozal https://kinozal.tv/ | Torznab + `kinozal.yml` | Account | Login/settings implementation inspected | Disable `addrussian`; card auth/live download not tested |
| NNM-Club https://nnmclub.to/ | Concrete anonymous `nnmclub` search/card/download-handle adapter; Torznab remains optional | Anonymous search/card where site permits | 2026-09-09: live search `Big Buck Bunny` returned 5 results; Windows-1251 parser fixture passes | Card request returned `TEMPORARY_FAILURE` twice from this network; no `download.php` request was made. Live card/download still outstanding |
| LostFilm https://lostfilm.tv/ | Torznab + `LostFilm.cs` | Account/captcha | Login implementation inspected | Live numbering/quality/download tests outstanding |
| NewStudio https://newstudio.tv/ | Torznab + `newstudio.yml` | Definition public; IP restrictions | Search/download selectors inspected | Upstream warns IP filtering; live unavailable/unverified |
| AlexFilm https://alexfilm.tv/ | `studio_matching_only` in engine | Site offers Google/Yandex login | Public home page reachable via web tool; catalog and studio channel links visible | Direct catalog adapter not implemented; no download interface verified |
| Red Head Sound https://redheadsound.studio/ | `studio_matching_only` in engine | Undetermined | Public fetch returned 403 via web tool | Direct integration blocked for this access method; no bypass |
| Flarrow Films https://fla-fi.studio/ | `studio_matching_only` in engine | Undetermined | Address linked by official Telegram channel; public catalog reachable via web tool | Older official posts mention torrents, so absence must NOT be asserted; current download/card integration not tested |

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
