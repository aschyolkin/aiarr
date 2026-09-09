# Adapter SDK

`mae.domain.Provider` defines typed operations. `SearchResult` has public candidate
metadata, `ReleaseDetails` has cleaned evidence and file lists, `DownloadHandle`
holds a SecretStr and is never passed to LLM. Keep credentials per adapter.

See `TorznabProvider` and `JSONProvider` in `mae/providers.py`, and
`test_new_protocol_same_contract`. The JSON protocol is specifically for a
user-owned source implementing this documented contract; not a claim about sites:

```
GET <configured-url>?operation=search&q=Example
{"results":[{"release":{"release_id":"42","title":"Example.2024.1080p.WEB-DL.H264","size":1000},
             "description":"Audio: Russian","download":"https://approved-source/path.torrent"}]}
GET <configured-url>?operation=health
{"status":"OK"}
```

New protocol implementation goes in provider layer and factory registration;
worker, scoring and import do not change. Adapter must raise ProviderError with
explicit Outcome; absent results are an empty list, never a parser-error fallback.
No raw links in evidence. Source HTTP validates and pins public IPs, forbids
redirects and caps decompressed response bytes. Gateway private-network access
requires exact configured origin. Automatic redirect handling is deliberately
unsupported until an explicit allowlist implementation is tested.

Site-specific adapters may be stricter: the built-in anonymous `rutor` adapter
requires the exact configured HTTPS `source_host` and its card adapter; `nnmclub`
requires `https://nnmclub.to`. A mirror needs an explicit, tested adapter change;
it is not a configuration-only fallback.

Some operator networks intentionally resolve an external source to a Fake-IP for
their own proxy. Set `trusted_source: true` only for that exact configured HTTPS
`source_host`. This is an explicit operator trust boundary: it permits that one
origin to use the local resolver, while redirects, other domains and untrusted
sources remain pinned and rejected. It is never inferred from DNS results.
