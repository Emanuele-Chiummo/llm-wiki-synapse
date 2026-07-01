# ADR-0043 — UI-settable CLI subscription OAuth token (DB-stored, injected into the spawned `claude` CLI, API key scrubbed from the child env) (F17, I6/I7, §12)

- **Status:** Accepted
- **Date:** 2026-07-01
- **Sprint:** v0.6 (Amendment — owner request: set the CLI provider's Claude subscription OAuth token from the UI, not env-only)
- **Feature:** F17 (Inference Provider — CLI backend `CliAgentProvider`) · runtime-configuration parity with the clip token (ADR-0040) and MCP token (ADR-0033)
- **Invariants owned:** **I6** (nothing hardcoded — no provider/model/backend literal; routing still via `capabilities()`) · **I7** (bounded loops + truthful cost — unchanged; subscription = $0 marginal, ADR-0009) · **I8** (D2 ER + D4 OpenAPI regenerated) · **I9** (reuse `vault_state` + the existing `CliAgentProvider`/`claude-agent-sdk` — no new process, no new dep) · **§12** (secrets via env only — **narrowly amended for this one credential**, §2.1)
- **Related / supersedes:** **Amends ADR-0042 §1** (the pure-env resolver precedence) for the CLI provider — a DB-set token now takes precedence over env, and the token **value** is now read and injected (ADR-0042 forwarded no value). **Mirrors ADR-0040** (clip token: DB-plaintext, DB-wins-over-env, GET returns posture only) and **ADR-0033** (`vault_state` credential column, one-time posture, never returned by GET). ADR-0009 (subscription = $0.00 cost convention — unchanged). ADR-0007 (InferenceProvider ABC + capability routing). ADR-0008 §3 / §12 (secrets-via-env — the rule this ADR narrowly scopes).
- **Author:** solution-architect
- **Implementers:** backend-engineer (column + migration `0017` + `_CliAuthConfigCache` + `app/cli_auth.py` resolver + `GET/PUT /provider/cli-auth` + `ProviderSettings.subscription_token` + tests) · ai-agent-engineer (inject the token into the spawned CLI env + scrub `ANTHROPIC_API_KEY` from the child env in `cli.py`; wire `resolve_provider` to stamp the resolved token; auth-mode precedence tests) · frontend-engineer (Settings CLI-auth password field + Save/Clear + posture + mini-guide + i18n) · tech-writer (D2 ER regen, D4 OpenAPI, DEPLOY note, README row + amend ADR-0042 with a "Superseded in part" cross-ref)

---

## 1. Context

ADR-0042 (commit `31269dd`) let the F17 CLI provider (`CliAgentProvider`, `backend/app/ingest/provider/cli.py`) run on a Claude Pro/Max **subscription** instead of a pay-per-token `ANTHROPIC_API_KEY`, including inside the Docker container, via a **pure-env** resolver `_resolve_cli_auth_mode()` with three signals (`ANTHROPIC_API_KEY` > `CLAUDE_CODE_OAUTH_TOKEN` > `CLAUDE_CODE_USE_SUBSCRIPTION`). It deliberately **detected presence only** and **never read or forwarded a token value** (§12 in its strict form): the SDK's spawned `claude` CLI inherited `CLAUDE_CODE_OAUTH_TOKEN` from the process environment.

**Owner request (accepted, 2026-07-01):** set the subscription OAuth token — the `sk-ant-oat01-…` value produced by `claude setup-token` on the host — **from the web UI** (Settings, when the Claude CLI provider is selected), instead of only via `.env` + container restart. The owner has **explicitly accepted storing it in the DB in plaintext.** This brings the CLI subscription credential to parity with the clip token (ADR-0040) and MCP token (ADR-0033), which are already UI-settable and stored in `vault_state`.

**Why this needs an ADR (the §12 tension).** CLAUDE.md §12 / ADR-0008 §3 say "**no secrets in code or database**; API keys are environment-only." This ADR must (a) read the token **value** (ADR-0042 never did) and (b) **store it in Postgres**. Both cross §12. This is a deliberate, owner-accepted trade for one credential, ratified here with the security posture kept explicit and the §12 amendment scoped **as narrowly as possible**.

**Two facts that shape the design (from ADR-0042 §Context, re-verified):**
1. **The subscription is the OAuth path; the API key outranks it.** Claude Code's own documented credential precedence puts `ANTHROPIC_API_KEY` **above** the subscription — if it is set (even empty) in the spawned CLI's environment, it wins and bills per token. For a DB-set subscription token to actually be used, `ANTHROPIC_API_KEY` must be **absent from the child process's environment**.
2. **The token must be recoverable to be usable.** Unlike the MCP token (ADR-0033, verified against an *incoming* request — so a one-way salted hash suffices) and unlike the clip token where a hash would also work, the subscription OAuth token is **replayed outbound** into the spawned `claude` CLI. Synapse must hand the CLI the *actual token value*. A hash is therefore **impossible** here — the credential must be stored recoverable.

---

## 2. Decision

### 2.1 The CLI subscription token is stored PLAINTEXT in `vault_state`; §12 is amended NARROWLY for this one credential

**Why plaintext, not hashed (the load-bearing security reasoning).** ADR-0033 stores the MCP token as a PBKDF2 salted hash and ADR-0040 could have too, because those tokens are **verified against an incoming bearer** — Synapse only needs to check a presented value against a stored one, so it can store a one-way hash and never keep the plaintext. The CLI subscription token is **categorically different: it is replayed OUTBOUND** — Synapse injects the literal token into the spawned `claude` CLI's environment as `CLAUDE_CODE_OAUTH_TOKEN`. A one-way hash cannot be replayed. Therefore the token **must be stored recoverable (plaintext)**; there is no hashed alternative that preserves the feature. (This is the same reason ADR-0040 §2.2 keeps the *env* clip token plaintext: a credential Synapse must present, not merely verify, cannot be hashed at rest.)

**Blast radius — and why it is HIGHER than the clip token.** Storing the token plaintext in `vault_state` means a **DB dump / `pg_dump` / backup leak yields the live credential** — exactly the risk §12 exists to prevent. We accept it with eyes open, but we note it is **strictly worse than the clip/MCP case**: the clip token grants "write one vault via `POST /clip`"; the **CLI subscription token grants access to the user's entire Claude Pro/Max subscription** (any Claude Code capability the token authorizes, until it expires or is revoked). The blast radius is therefore **≈ an `.env` leak of `CLAUDE_CODE_OAUTH_TOKEN`** — the token was *already* going to live in `.env`/process-env under ADR-0042 (equally recoverable there); moving it to the DB does not create a new class of exposure, but it **adds `pg_dump`/DB-backup to the leak surface** that `.env` alone did not have. The owner has weighed this and accepted it for the UI convenience.

**§12 amendment (scoped to exactly one column).** §12 / ADR-0008 §3 continue to forbid storing **third-party provider API keys** (`ANTHROPIC_API_KEY`, OpenAI-compatible keys) in code or DB — those remain env-only, unchanged. This ADR carves out **one** narrow exception: **`vault_state.cli_oauth_token`** may hold the Claude subscription OAuth token in plaintext, because (a) it is *replayed*, not *verified*, so it cannot be hashed, and (b) the owner has explicitly accepted the trade. **No other secret gains a DB home.** The `ApiProvider` API key path is untouched and stays env-only.

**Rotation & revocation.** There is **no server-side rotation** (Synapse does not mint this token — `claude setup-token` on the host does). Rotation = the owner runs `claude setup-token` again and **pastes the new value** (overwrites the column), or **clears** it (`PUT … {clear:true}` → column `NULL`). Revocation of a leaked token is done at Anthropic (the owner revokes the OAuth grant); clearing the column stops Synapse replaying it. The ~1-year token lifetime from ADR-0042 is unchanged.

**Never logged, never returned.** `cli_oauth_token` is **never logged** (not at load, not on PUT, not on inject) and is **never returned by any GET or PUT** (posture only — §2.5). This is the load-bearing invariant, tested explicitly.

### 2.2 Storage — one nullable column on `vault_state` via Alembic `0017`

Add **`vault_state.cli_oauth_token : Mapped[str | None]`** (`Text`, nullable, default `NULL`) — mirroring the `clip_access_token` shape (ADR-0040) but documented as **plaintext, replayed outbound, never hashed**.

| Kind | Name | Type / default | Where | Notes |
|---|---|---|---|---|
| DB column | `vault_state.cli_oauth_token` | `TEXT NULL` | `models.py` `VaultState` | **NEW — Alembic `0017`.** Plaintext Claude subscription OAuth token (`sk-ant-oat01-…`, `claude setup-token`). NULL = no UI token; env governs. When set, injected into the spawned `claude` CLI as `CLAUDE_CODE_OAUTH_TOKEN` and takes precedence over env. **NEVER logged; NEVER returned by any endpoint.** |
| migration | `0017_vault_state_cli_oauth_token` | add 1 column | `backend/alembic/versions/` | `revision = "0017"`, `down_revision = "0016"`. Downgrade drops the column. `make er` regenerated (I8). |

Column comment (implementer copies verbatim into `models.py` and the migration):

> Plaintext Claude subscription OAuth token for the CLI provider (ADR-0043 §2.1). Produced on the host by `claude setup-token` (`sk-ant-oat01-…`). NULL = no UI token; env `CLAUDE_CODE_OAUTH_TOKEN` / `CLAUDE_CODE_USE_SUBSCRIPTION` govern. When NOT NULL the DB value is authoritative: it is injected into the spawned `claude` CLI's env as `CLAUDE_CODE_OAUTH_TOKEN` AND `ANTHROPIC_API_KEY` is scrubbed from that child env so the subscription wins (ADR-0043 §2.3). Stored PLAINTEXT because it is replayed outbound to the CLI, not verified against an incoming request — a hash cannot be replayed (§12 narrowly amended for this one credential). NEVER logged; NEVER returned by any endpoint. Migration 0017.

`make er` regenerated to match the live schema (I8). Migration-free everywhere else.

### 2.3 Resolution precedence — "DB wins over env" (supersedes ADR-0042 §1 for the CLI provider); the injection + API-key scrub is the safety crux

ADR-0042's resolver was pure-env with `ANTHROPIC_API_KEY` first. This ADR **prepends a DB tier** and makes it **outrank the env API key**, because a DB token set from the UI is an **explicit, deliberate operator action** ("use my subscription for the CLI"), and injection lets us **guarantee** the subscription is actually used (not silently out-billed by a stray env API key). The new precedence in `_resolve_cli_auth_mode()` (still called **before any SDK stream/loop opens**, Do-NOT #9):

1. **DB `cli_oauth_token` set (non-empty) → `"subscription"`.** The provider **injects `CLAUDE_CODE_OAUTH_TOKEN=<db value>` into the spawned CLI's env** AND **scrubs `ANTHROPIC_API_KEY` from that child env** (removes the key from the child's environment dict). *This scrub is the safety crux:* without it, Claude Code's own precedence (§1 fact 1) would let an inherited `ANTHROPIC_API_KEY` outrank the injected subscription token and **bill per token** despite the deliberate UI action. The scrub makes the subscription deterministically win.
2. else **env `ANTHROPIC_API_KEY` non-empty → `"api-key"`** (billed per token; real cost recorded — **unchanged** from ADR-0042).
3. else **env `CLAUDE_CODE_OAUTH_TOKEN` non-empty → `"subscription"`** (inherit from process env; **unchanged** — no injection needed, no scrub needed because tier 2 already established there is no env API key).
4. else **env `CLAUDE_CODE_USE_SUBSCRIPTION` truthy → `"subscription"`** (ambient host login / mounted creds; **unchanged**).
5. else **clean `ValueError`** naming all options (**unchanged**).

**Why DB-token above env-API-key is correct (ratified).** Tier 1 above tier 2 is a **deliberate reordering** relative to ADR-0042's env-only order. It is right because: (a) setting the token in the UI is an unambiguous, explicit intent to use the subscription, so it should not be silently overridden by a bootstrap env var; (b) we can only *guarantee* that intent by injecting the token AND scrubbing the API key from the child env — presence-detection alone (ADR-0042) could not, because it never touched the child env. An operator who genuinely wants API-key billing simply does not set (or clears) the DB token — then tiers 2–4 behave byte-for-byte as ADR-0042. **The empty-string=unset rule from ADR-0042 still holds** at every tier: an empty `cli_oauth_token` or empty `ANTHROPIC_API_KEY` is treated as unset (so a stray `export ANTHROPIC_API_KEY=""` cannot silently outrank the subscription).

**Scope of the scrub (explicit).** The API-key scrub applies **only to the child environment dict Synapse builds for the spawned CLI**, and **only** on tier 1 (DB-token path). Synapse **NEVER mutates the parent `os.environ`** (Do-NOT). Tiers 2–4 build no injected env (they inherit), so no scrub occurs there — correct, because tier 2 by definition means the operator chose the API key.

### 2.4 Injection mechanism — the token reaches `cli.py` on `ProviderSettings`, never via DB access in `cli.py`

**Layering constraint (hard).** `cli.py` (and the whole `app/ingest/provider/` package) must **not** acquire DB access — the package is deliberately ORM-free (`config.py` docstring: "the provider package never imports `models.py`"). So the DB token value must be **resolved at a site where a DB-derived value is already available** and **carried into `cli.py` on the settings object**.

**Carrier — new field on `ProviderSettings`:**

```python
@dataclass(frozen=True)
class ProviderSettings:
    ...
    # ADR-0043: the resolved Claude subscription OAuth token for the CLI backend, injected
    # into the spawned `claude` CLI env as CLAUDE_CODE_OAUTH_TOKEN when set. None = no DB token
    # (env governs). Set ONLY for provider_type == "cli"; ignored by local/api. NEVER logged.
    subscription_token: str | None = None
```

**Field name:** `subscription_token` · **Type:** `str | None` · **Default:** `None`.

**Where it is populated (the DB-session-free resolution site).** The token lives in `vault_state`, not `provider_config`, and `resolve_provider(row)` is **synchronous** and called from ~7 sites (orchestrator, chat/stream, ops/*). To keep every call site unchanged AND keep `resolve_provider` synchronous AND avoid a DB round-trip per provider build, the DB token is served from an **in-process cache loaded at startup** (mirroring `_ClipConfigCache`), fronted by a tiny cycle-free resolver module:

- **New module `backend/app/cli_auth.py`** — imports only `app.config` (env fallback signals) + stdlib; holds the module-level singleton `_cli_auth_config_cache: _CliAuthConfigCache` and exposes `resolve_subscription_token() -> str | None` (returns the cached DB token if non-empty, else `None` — env is handled inside `cli.py`'s resolver, this only surfaces the DB tier). `config.py` imports no app modules, so this module is **import-cycle-free** and safely importable from both `main.py` and `app/ingest/provider/__init__.py`.
- **`resolve_provider()` (`app/ingest/provider/__init__.py`) stamps the token** onto `ProviderSettings` **only when `provider_type == "cli"`**: it calls `cli_auth.resolve_subscription_token()` and passes the result as `subscription_token=` into `_settings_from_row(...)` (or a post-construction copy). Non-CLI providers get `subscription_token=None`. **All 7 existing `resolve_provider(row)` call sites stay unchanged** (the token is resolved internally). This is the single injection point.
- **`_CliAuthConfigCache`** (in `main.py`, mirroring `_ClipConfigCache`): holds `_token: str | None` under an `asyncio.Lock`; `load(token)` at startup, `set_token(token)` on PUT. Accessors: `get_token() -> str | None` (NEVER log/return outside injection), `token_source() -> "db"|"env"|"none"`, `token_configured() -> bool`, `auth_mode() -> "api-key"|"subscription"|"unconfigured"` (§2.5). The singleton lives in `main.py`; `app/cli_auth.py` holds the **canonical** singleton and `main.py` imports it (so the class may live in `cli_auth.py` to avoid `provider/__init__.py → main.py`; implementer places `_CliAuthConfigCache` **in `app/cli_auth.py`** and `main.py` imports `_cli_auth_config_cache` + the load/set helpers). Loaded once in the lifespan via `_load_cli_auth_config_cache()` right after `_load_clip_config_cache()`.

**What `cli.py` does with it (ai-agent-engineer).** `cli.py`'s `_resolve_cli_auth_mode()` gains the DB tier (§2.3 tier 1) by reading `config.subscription_token` (passed in — **no DB import**), and when it is set:
- builds a **child env dict** for `ClaudeAgentOptions` = a copy of the relevant inherited env **plus** `CLAUDE_CODE_OAUTH_TOKEN=<config.subscription_token>` **minus** `ANTHROPIC_API_KEY` (scrubbed), and passes it via **`ClaudeAgentOptions(env={...})`**.
- **SDK env param — verify + fallback.** ai-agent-engineer MUST verify the installed `claude-agent-sdk` (`>=0.2,<0.3`, ADR-0042 §3) exposes an `env` (or equivalently named `environment`) parameter on `ClaudeAgentOptions` that sets the **spawned CLI subprocess** environment. **If present:** use it (this is the required, `os.environ`-safe path). **If absent (fallback):** wrap **only the `ClaudeSDKClient(options=…)` context** in a scoped, restored-in-`finally` environment override (a context manager that sets `CLAUDE_CODE_OAUTH_TOKEN` and deletes `ANTHROPIC_API_KEY` for the duration of the SDK session, then restores the prior values) — **still never a permanent `os.environ` mutation**, and documented in `cli.py` as the SDK-limitation fallback. The `env`-param path is strongly preferred; the fallback exists only if the SDK offers no per-spawn env hook. Either way: **the parent `os.environ` is never permanently mutated** (Do-NOT).
- `_resolve_cli_auth_mode()` gains a parameter (e.g. `subscription_token: str | None`) rather than reading a global — keeping it a pure function of (env + injected token) and its existing infra-free tests trivial to extend.

### 2.5 Endpoints — `GET/PUT /provider/cli-auth` (posture only; user PASTES the token; no server-side generation)

Mirrors `GET/PUT /clip/config` (ADR-0040) but **simpler**: the user pastes their own token, so there is **no server-generated token, no `generated_token`, no rotate/one-time-reveal**. Set = store the pasted value; clear = null it.

**`GET /provider/cli-auth`** — read-only posture; **NEVER the token value**:
```
GET /provider/cli-auth
  response: CliAuthConfigResponse {
    token_configured: bool,                                  # DB or env signal present
    token_source:     "db" | "env" | "none",
    auth_mode:        "api-key" | "subscription" | "unconfigured"
  }
```
- **`token_source`:** `"db"` iff `cli_oauth_token` column is set (non-empty); else `"env"` iff **any** env signal is present (`ANTHROPIC_API_KEY` OR `CLAUDE_CODE_OAUTH_TOKEN` OR `CLAUDE_CODE_USE_SUBSCRIPTION` truthy); else `"none"`.
- **`auth_mode`** (derived from the §2.3 precedence, presence-only — does not run the injection): DB token set → `"subscription"`; else env `ANTHROPIC_API_KEY` non-empty → `"api-key"`; else (`CLAUDE_CODE_OAUTH_TOKEN` non-empty OR `CLAUDE_CODE_USE_SUBSCRIPTION` truthy) → `"subscription"`; else → `"unconfigured"`.
- **`token_configured`** = `token_source != "none"`.

**`PUT /provider/cli-auth`** — set or clear; returns the **post-write posture** (no value):
```
PUT /provider/cli-auth
  request body: CliAuthConfigRequest {
    token?: str,     # set: store this pasted token into vault_state.cli_oauth_token
    clear?: bool     # true ⇒ set cli_oauth_token = NULL (fall back to env / none)
  }
  response body: CliAuthConfigResponse   # same shape as GET; NEVER the token value
```
Semantics + status codes:
- **`clear:true`** → column `NULL`; refresh cache; **200** with post-write posture. (`clear` wins if both sent — deterministic.)
- **`token:"<value>"`** → **validate**, then store to `cli_oauth_token`; refresh cache; **200** with post-write posture.
- **Validation (deliberately lenient — the owner pastes a real token; the CLI is the true validator).** Reject with **422** only on clearly-wrong input: empty/whitespace-only string, or length outside a sane band (e.g. `< 20` or `> 500` chars). A **soft prefix check** — warn-but-accept if it does not start with `sk-ant-oat01-` — because Anthropic may change the prefix and we must not hard-block a token the CLI would accept; the *format* is not ours to gatekeep. (Rationale: a too-strict server-side format check would out-guess Anthropic and break on prefix changes; a length+non-empty floor catches paste errors without over-fitting.) Recommended: **accept** any non-empty, length-sane string; return **422** only for empty/whitespace or absurd length; do **not** hard-reject on prefix.
- **Neither `token` nor `clear` (empty body)** → **400** (nothing to do) — mirror the clip/web-search "no-op request" handling if that pattern returns 400 there; otherwise **200** no-op is acceptable (implementer aligns with the existing `PUT /clip/config` empty-body behavior for consistency).
- **Endpoint auth:** same-origin / unauthenticated, consistent with `PUT /clip/config` (ADR-0040 §2.3) and `PUT /mcp/auth` (ADR-0033 §2.5) — the network perimeter is the outer gate; the Settings UI is same-origin. Stated trade-off identical to ADR-0040.
- **Cache:** in-process `_CliAuthConfigCache` singleton (§2.4), loaded at startup, **refreshed on every PUT**. The provider factory reads the DB token from this cache O(1) — no per-build DB round-trip.

### 2.6 UI (frontend-engineer — specification)

Under the **CLI provider** in Settings, in the **API + MCP** area (the coherent home — it already hosts credential/connection posture panels per ADR-0027/0032/0033; the LLM Models section is provider-list CRUD and is the wrong home for a per-backend credential). Add a **CLI Subscription Auth** sub-block (local component state + fetch/PUT only — no Zustand, mirrors ADR-0033/0040 UI, I3):
- A **password field** (masked) for the pasted token + **Save** (`PUT {token}`) + **Clear** (`PUT {clear:true}`) buttons. When a token is configured, show **only** `token_configured=true` + `token_source` + `auth_mode` — **never** the value (there is no reveal; GET never returns it).
- **Posture** row from `GET /provider/cli-auth`: `token_source` (`db`/`env`/`none`) and `auth_mode` (`subscription`/`api-key`/`unconfigured`), so the owner can see at a glance which credential wins.
- A **mini-guide** (i18n): run `claude setup-token` on the host → paste the printed `sk-ant-oat01-…` token here → **leave `ANTHROPIC_API_KEY` unset**, with the note: *"The DB token is injected into the Claude CLI and the API key is scrubbed from its environment, so the subscription wins even if `ANTHROPIC_API_KEY` is set elsewhere — but leaving it unset is clearest."*
- A **security caveat** (i18n): this token is stored in the database in plaintext and grants your Claude subscription; treat DB backups accordingly; to rotate, re-run `claude setup-token` and paste the new value, or Clear to fall back to env.
- i18n keys under `settings.cliAuth.*` in `en.json` + `it.json` (**parity gate**). The token value is **never** an i18n key or a rendered string.

---

## 3. Per-agent file ownership

**backend-engineer:**
- `backend/app/models.py` — add `cli_oauth_token` (`Text`, nullable, comment per §2.2) to `VaultState`.
- `backend/alembic/versions/0017_vault_state_cli_oauth_token.py` — add the column (`revision="0017"`, `down_revision="0016"`); downgrade drops it.
- `backend/app/cli_auth.py` (**new**) — `_CliAuthConfigCache` (mirrors `_ClipConfigCache`: `_token` + `asyncio.Lock`; `load`/`set_token`/`get_token`/`token_source`/`token_configured`/`auth_mode`), the module-level singleton, `resolve_subscription_token() -> str | None`, and `_load_cli_auth_config_cache()`. Imports only `app.config` + stdlib (cycle-free).
- `backend/app/ingest/provider/config.py` — add `subscription_token: str | None = None` to `ProviderSettings`.
- `backend/app/ingest/provider/__init__.py` — `resolve_provider()` stamps `subscription_token` from `cli_auth.resolve_subscription_token()` **only when `provider_type == "cli"`**.
- `backend/app/main.py` — `GET /provider/cli-auth` + `PUT /provider/cli-auth` (`CliAuthConfigRequest`/`CliAuthConfigResponse`); call `_load_cli_auth_config_cache()` in the lifespan after `_load_clip_config_cache()`; import the `cli_auth` singleton. Never log/return the token.
- `backend/tests/` — token never returned by GET/PUT (grep + response assertions); `token_source`/`auth_mode` precedence matrix; PUT set/clear round-trip refreshes cache; empty/whitespace/absurd-length → 422; DB-token-wins-over-env-API-key at resolution.

**ai-agent-engineer (F17 — the injection + scrub):**
- `backend/app/ingest/provider/cli.py` — extend `_resolve_cli_auth_mode()` to take the injected `subscription_token` and implement §2.3 tier 1 (DB token → `"subscription"`); build the child env dict (`+CLAUDE_CODE_OAUTH_TOKEN`, `−ANTHROPIC_API_KEY`) and pass it via `ClaudeAgentOptions(env=...)` in **both** `delegate_ingest()` and `_chat_stream()`; **verify the SDK `env`/`environment` param** and implement the scoped-restore fallback if absent (§2.4); **never mutate parent `os.environ`**. Read `config.subscription_token` from the `ProviderSettings` passed to `__init__` — **no DB import in `cli.py`**.
- `backend/tests/` — DB-token path selects `"subscription"` even with `ANTHROPIC_API_KEY` set in env (scrub proven: the child env has no `ANTHROPIC_API_KEY` and has the injected `CLAUDE_CODE_OAUTH_TOKEN`); empty DB token treated as unset; parent `os.environ` unchanged after a run; env-only tiers 2–4 unchanged (ADR-0042 tests stay green).

**frontend-engineer:**
- `frontend/src/api/providerClient.ts` (or the coherent API client) — `CliAuthConfigResponse`/`CliAuthConfigRequest` + `getCliAuth()`/`setCliAuth()`.
- `frontend/src/components/settings/SettingsPanel.tsx` — the CLI Subscription Auth sub-block in the API + MCP section (password field + Save/Clear + posture + mini-guide + caveat; local state, I3).
- `frontend/src/i18n/en.json`, `it.json` — `settings.cliAuth.*` (parity). Token value never rendered when configured.
- `frontend/src/tests/` — vitest: token never rendered/returned; posture reflects `token_source`/`auth_mode`; Save/Clear PUT shapes.

**tech-writer:**
- `docs/er/schema.mmd` — `make er` (D2; new `cli_oauth_token` column).
- `docs/api/openapi.json` — `make openapi` (D4; `GET/PUT /provider/cli-auth`).
- `docs/DEPLOY.md` — note the token is now UI-settable (env stays a bootstrap fallback; DB wins); document the plaintext-in-DB posture + backup caveat + rotation (re-`setup-token` + paste, or Clear).
- **Amend ADR-0042** — add a "Superseded in part by ADR-0043 (§1 precedence + value handling)" cross-ref line (do not rewrite it).
- `docs/adr/README.md` — header line + new `0043` table row.

**devops-engineer:** one-line note — migration applied by standard `alembic upgrade head`; no new service/port/env.

---

## 4. Acceptance checks (DoD)

1. **Token never returned.** `GET /provider/cli-auth` (and `PUT`) with a DB token set returns `token_source="db"`, `token_configured=true`, `auth_mode="subscription"`, and the token value **never** appears in any response body; grep proves it is never logged.
2. **Set/clear round-trip.** `PUT {token:"sk-ant-oat01-…"}` stores it and refreshes the cache (posture flips to `db`/`subscription`); `PUT {clear:true}` nulls it and posture falls back to env/none.
3. **DB stored plaintext, replayable.** After set, `vault_state.cli_oauth_token` equals the pasted value verbatim (no hash) — this is intended and asserted.
4. **DB wins over env API key (the crux).** With `cli_oauth_token` set AND `ANTHROPIC_API_KEY` set in env, `_resolve_cli_auth_mode()` returns `"subscription"`, the spawned CLI's child env contains the injected `CLAUDE_CODE_OAUTH_TOKEN` and **no `ANTHROPIC_API_KEY`** (scrub proven), and cost logs at INFO (ADR-0009 $0).
5. **Parent env untouched.** After a CLI run on the DB-token path, `os.environ["ANTHROPIC_API_KEY"]` (if it was set) is unchanged and `CLAUDE_CODE_OAUTH_TOKEN` is not permanently injected into the parent.
6. **Empty = unset.** An empty/whitespace `cli_oauth_token` is treated as unset (env governs); empty `ANTHROPIC_API_KEY` still cannot outrank the subscription (ADR-0042 rule preserved).
7. **Precedence matrix.** `token_source`/`auth_mode` correct for every combination of {DB set/unset} × {`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_USE_SUBSCRIPTION`} env presence.
8. **Validation.** Empty/whitespace or absurd-length token → 422; a plausible token → 200; prefix mismatch is accepted (soft, not hard-blocked).
9. **Layering.** `grep` proves `cli.py` (and `app/ingest/provider/*`) import no DB/`models`; the token reaches `cli.py` only via `ProviderSettings.subscription_token`.
10. **SDK env param.** A test/assertion documents whether `ClaudeAgentOptions` exposes `env`; the injection uses it, or the scoped-restore fallback, with parent env restored in `finally`.
11. **ADR-0042 tests green.** Env-only tiers 2–4 behave byte-for-byte as before.
12. **Docs gate.** `make er` matches live schema (new column); `make openapi` includes `GET/PUT /provider/cli-auth`; ADR-0042 amendment cross-ref added; i18n en/it parity (I8).

---

## 5. Consequences

**Positive** — the owner sets the Claude subscription OAuth token from the UI (no `.env` edit, no container restart), at parity with the clip (ADR-0040) and MCP (ADR-0033) tokens. The **injection + API-key scrub guarantees** the subscription is actually used (deterministic $0 billing), closing ADR-0042's "operator must remember to unset `ANTHROPIC_API_KEY`" foot-gun for the DB-token path. Reuses `vault_state`, `CliAgentProvider`, and `claude-agent-sdk` — no new process, no new dep (I9). Env tiers stay as bootstrap fallback (zero breakage; ADR-0042 deployments untouched).

**Trade-offs (explicit)** —
- **Plaintext secret in Postgres** — a DB dump/backup leak yields the live subscription credential (higher value than the clip token; blast radius ≈ `.env` leak **plus** the DB-backup surface). This is the §12 trade the owner explicitly accepted; it is **unavoidable** because the token is replayed, not verified (a hash cannot be replayed). Documented in the UI caveat and DEPLOY.
- **One migration, one column** (D2/ER regen).
- **No server-side rotation/reveal** — the owner rotates by re-running `claude setup-token` and pasting; a lost token is re-pasted, not recovered from the UI (never displayed). More secure UX by omission.
- **Single-process cache** — on a multi-process deploy the cache is stale after PUT until restart. Acceptable for a personal single-process homelab (same as ADR-0040 §5).
- **SDK env-param dependency** — the clean path needs `ClaudeAgentOptions(env=…)`; the scoped-restore fallback covers its absence but is heavier. Verified by ai-agent-engineer.

**Invariant check** — **I6:** no provider/model/backend hardcoded; routing still by `capabilities()`; `subscription_token` is data, not a routing decision. **I7:** loops/cost unchanged; subscription = $0 (ADR-0009). **I8:** migration → D2/ER regen + D4 OpenAPI + ADR-0042 amendment. **I9:** reuses `vault_state` + `CliAgentProvider` + the SDK — no new process/dep. **§12:** narrowly amended for exactly one column (`cli_oauth_token`), justified by replay-not-verify + owner acceptance; all third-party provider API keys stay env-only. **I1/I2/I3/I4/I5:** untouched (no ingest/graph/render/editor/vault-format change).

---

## 6. Do-NOT

1. Do **not** hash `cli_oauth_token` — it is replayed to the CLI, not verified; a hash cannot be replayed (§2.1). (This is the one place plaintext is correct.)
2. Do **not** return or log the token value anywhere — GET/PUT expose posture only; no reveal, ever (§2.1/§2.5).
3. Do **not** mutate the parent `os.environ` — inject via `ClaudeAgentOptions(env=…)` or a scoped-restore context; restore in `finally` (§2.4).
4. Do **not** let `cli.py` (or `app/ingest/provider/*`) import DB/`models` — the token arrives only on `ProviderSettings.subscription_token` (§2.4).
5. Do **not** skip the `ANTHROPIC_API_KEY` scrub on the DB-token path — without it the API key can out-bill the subscription (§2.3 crux).
6. Do **not** treat an empty `cli_oauth_token` or empty `ANTHROPIC_API_KEY` as "set" (ADR-0042 rule preserved — §2.3).
7. Do **not** generate a token server-side or add a `generated_token`/rotate-reveal — the user pastes their own (§2.5).
8. Do **not** hard-block on token prefix/format — soft-check only; the CLI is the real validator (§2.5).
9. Do **not** widen the §12 exception beyond this one column — provider API keys stay env-only (§2.1).
10. Do **not** open an SDK stream/loop before `_resolve_cli_auth_mode()` — a misconfig must fail pre-stream (Do-NOT #9, ADR-0042).
