# Reuse audit — 2026-09-08

Sources were cloned into ignored `.audit/`, implementation read, not just README.
SHAs below are audit snapshots, not claims about installed package versions.
Runtime pins and transitive hashes are in `uv.lock`.

| Upstream / SHA | Inspected modules; license | Decision and verification |
|---|---|---|
| Harrsn/faucet `286fcb042ddac007754fea19f0bae4aa01b70c46` | `faucet/db.py`, `importer.py`, `stalls.py`, `clients/qbittorrent.py`, `tests/test_faucet.py`; MIT, Harrison Korodi | Reference only, no extraction. Catalog/config globals and SQL tables coupled; destructive default stall removal unsuitable. Existing test file: 106 passed. |
| dreulavelle/rank-torrent-name `bdb9973109eb489be831af0c39bdf9c27e3378ed` | `RTN/parser.py`, `ranker.py`, `models.py`; MIT, Spoked | Candidate, not runtime dependency: adds a second parser and media libraries; scoring without evidence cannot authorize selection. Parser/ranker/settings tests: 142 passed. |
| guessit-io/guessit `3c73072b8971d412935c6f749be63c564dd41385` | `guessit/api.py`, rules, `guessit/test/test_api*.py`; LGPL-3.0-or-later, Rémi Alvergnat and contributors | Unmodified Python dependency. Only preliminary title/video/episode extraction; no Russian audio inference from title. API tests: 30 passed. Original sources remain replaceable; no vendoring or static linking. |
| Viren070/AIOStreams `58000e86c959a3ab31c687c903fa09873ea7d808` | `packages/core/src/streams/precomputer.ts`, `filterer.ts`; AGPL-3.0 | Reference only, no code port. Additive scores and matched-rule explanations studied. Full Stremio context dependency unsuitable. Tests not run. |
| theantipopau/slimarr `eda1c9a96369552780467adfdc8dcfdcc20d6457` | `backend/core/search_diagnostics.py`, `recommendations/scoring.py`; MIT | Reference only. Diagnostics coupled to socket events and in-memory state. Recommendation scoring upstream tests: 24 passed. |
| Jackett/Jackett `5ee003621fc188f4e490601fac6a295762059121` | `src/Jackett.Common/Indexers/Definitions/{RuTracker,LostFilm}.cs`, `Definitions/{rutor,kinozal,newstudio}.yml`; GPL-2.0 | Optional separate service through Torznab, no embedded definitions. Login/captcha handled in its UI. .NET not available locally; live site/account validation outstanding. Disable synthetic Russian title suffixes. |
| Prowlarr/Indexers `06b9886d209b947b7a4425269c1e67fbde626f28` | `definitions/*/schema.json`, `scripts/validate.py`, README | Optional external runtime only. Repository has no root LICENSE at audited SHA; provenance must be resolved before copying definitions. No definitions copied. |
| rmartin16/qbittorrent-api `9972fbb319c4570f1548c88faa7316d624cec8cc` | `src/qbittorrentapi/client.py`, `torrents.py`, `tests/conftest.py`; MIT, Russell Martin | Unmodified dependency, disable automatic request retries around adds; reconcile hash first. Upstream offline invocation failed in collection because conftest contacts localhost:8080. Not a pass. Engine adapter tested separately. |
| seerr-team/seerr `6f5a17735d383b110cca04326ecd536ad7675ed6` | `server/api/servarr/{base,radarr,sonarr}.ts`, their tests and `seerr-api.yml`; MIT, sct and contributors | External service, independently implemented protocol facade. Installed version v3.4.1 must be contract-tested separately; develop audit is not live compatibility proof. |

## Updates

No upstream application modules copied. Update one dependency or gateway at a time:
record new SHA/license, inspect changed API and parser rules, regenerate lock, run
offline tests and isolated service E2E, compare abstention/false positives, then
stage deployment. Never auto-update source definitions or containers in production.
Logs from exploratory test commands remain in session; acceptance report distinguishes
tested subsets from entire upstream suites.
