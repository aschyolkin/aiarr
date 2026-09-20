# DeepSeek integration — 2026-09-20

The user approved upgrading the original V4 Flash requirement to V4.1 Flash.
The sole permitted model ID is now `deepseek-flash`, through the official
`https://api.deepseek.com/chat/completions` endpoint. No model fallback exists.
Old `MAE_DEEPSEEK_MODEL=deepseek-v4-flash` configuration must be replaced before
startup. The old model's cache namespace is not reused.

Official references checked on 2026-09-20:
- [Model retirement and current alias](https://api-docs.deepseek.com/quick_start/pricing/)
- [Thinking toggle](https://api-docs.deepseek.com/guides/thinking_mode/)
- [JSON output](https://api-docs.deepseek.com/guides/json_mode/)

Requests disable thinking and tools, request JSON, limit output to 1200 tokens,
and use a 30-second timeout with at most two attempts. Only sanitized evidence
is sent; validated quotes must refer to provided evidence. Failed or malformed
answers abstain. LLM-only mandatory claims still require review.

Set `MAE_DEEPSEEK_KEY` privately in the Custom App environment for the API and
worker, not in Git or logs. Default budget is 20 calls/day; failures also count.
Offline client tests verify the new request contract, caching, redaction,
bounded retries, and rejection of alternate model IDs. Live smoke testing
requires a real key and must be reported separately.

Live check passed on 2026-09-20 in deployed image `d442e18`: one official API
call, grounded Russian-audio assertion and a verified cache hit. The key is
configured privately on TrueNAS, not in the repository. Initial live responses
exposed a prompt defect: the model returned `Russian`, but validation required
`rus`. Prompt version 2 now supplies the same allowed values used by semantic
validation; validation was not relaxed. Billed tokens from rejected responses
are now counted too. No alternative model was used.

Run the explicit smoke check from the project root with the private key already
configured: `uv run python tests/live_deepseek.py --run-live`. It sends only
synthetic technical evidence, uses disposable SQLite state, validates grounded
Russian-audio extraction and cache reuse, and prints no tokens/credentials.
Exit 2 means missing key, not success; at most two billed calls are permitted.
