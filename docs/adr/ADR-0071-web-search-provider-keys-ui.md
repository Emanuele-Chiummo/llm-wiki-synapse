# ADR-0071 — UI-settable web-search cloud provider API keys (encrypted at rest)

- **Status:** Accepted
- **Date:** 2026-07-10
- **Feature:** F10 · v1.5 LLM Wiki parity (follows P3-e)
- **Relates:** [[ADR-0070]] (multi-provider web search), [[ADR-0043]] (UI-settable CLI token), ADR-0027 / W7 (Fernet at-rest encryption)

## Context

P3-e (ADR-0070) added opt-in cloud web-search providers (Tavily/SerpApi/Firecrawl/Brave). Their
API keys were **env-only** (`{PROVIDER}_API_KEY`), so a user selecting Tavily in the UI had no way
to actually configure it without editing the server environment. For a self-hosted app that is a
real usability gap. This ADR makes the keys settable from the UI **without ever storing plaintext**.

## Decision

1. **Encrypted at rest, reusing the W7 pattern.** Keys are stored in a single new column
   `vault_state.web_search_api_keys_encrypted` (BYTEA) — a Fernet-encrypted JSON map
   `{provider: key}`, master key from `SYNAPSE_SECRET_KEY` (`app/secrets_crypto.py`). This mirrors
   the CLI OAuth token (ADR-0043/ADR-0027). Alembic migration 0029.

2. **DB wins over env; cache-backed sync reads.** `app/ops/web_search/keys.py` resolves a key as
   DB-stored (decrypted into an in-memory cache at startup + after each write) → else the env var.
   The cache keeps the adapters' `configured()` / key reads synchronous.

3. **Never expose the value.** `GET /web-search/provider-keys` returns only a masked posture
   (`configured` + `source` ∈ db|env|none) per provider. The plaintext is never logged or returned.

4. **Fail-closed writes.** `PUT /web-search/provider-keys` requires `SYNAPSE_SECRET_KEY` to encrypt
   — returns HTTP 400 when absent (same contract as PUT /provider/cli-auth). The UI detects
   `secrets_available=false` and tells the user to set the master key or use env vars instead.

5. **UI.** When a cloud provider is selected in SectionWebSearch, a masked API-key field appears
   (password input + show/hide + Save + Remove + configured badge), styled with brand colours
   (never black; the "no master key" hint uses `var(--syn-amber)`).

## Consequences

- Cloud web-search providers are now fully configurable from the UI, at the same security bar as
  the CLI token (encrypted at rest, plaintext never surfaced).
- Requires `SYNAPSE_SECRET_KEY` to store keys via the UI; without it, env vars remain the path.
- One additive column; back-compatible (NULL → env vars govern).

## Tests

`backend/tests/test_web_search_keys.py` (resolver DB-over-env, masked posture never leaks the
value, secret-required write guard, adapter integration). Frontend: SettingsPanel test asserts the
key field appears for a cloud provider, is masked, and saves with the right args. Live cloud
round-trips remain unverified (no keys), as in ADR-0069/0070.
