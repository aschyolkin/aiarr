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
