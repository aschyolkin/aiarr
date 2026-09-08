# ADR 001: Small modular Python core, external indexer runtime

Accepted 2026-09-08 after implementation audit.

Faucet is closest functionally but owns a replacement catalog, global config,
direct SQLite schema, wanted/grabbed tables and title-based stall mapping. Its
default stale-torrent action removes partial files. Retrofitting evidence,
approved-only Seerr requests and journaled import would change most boundaries.
Slimarr assumes Plex/Usenet replacement and real-time diagnostics. AIOStreams is
bound to Stremio stream contexts and a different lifecycle.

Choose FastAPI/Pydantic + SQLAlchemy/Alembic, durable SQLite jobs and a single
worker process protected by an OS lock. Reuse GuessIt and qbittorrent-api as
unmodified packages; use Jackett or Prowlarr through protocol adapters. No HTML
selectors in orchestration or scoring. New protocol adapters implement the same
typed interface. Avoid promising live site coverage from a gateway listing.

Tradeoff: more new lifecycle code than a fork, but small independently testable
boundaries and no second user catalog. One worker is an explicit first-version
limit, not distributed lease support. Import journal and torrent hash survive
restart. Unknown evidence blocks automatic publication.
