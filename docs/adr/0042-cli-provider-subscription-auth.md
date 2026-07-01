# ADR-0042 — CLI provider subscription auth: OAuth token / ambient login, no API key (F17, I6/I7)

- Status: Accepted
- Date: 2026-07-01
- Sprint: v0.6
- Decider: solution-architect
- Invariants: I6 (pluggable inference — no hardcoded provider), I7 (bounded loops + truthful cost), §12 (secrets via env only)
- Related: ADR-0007 (InferenceProvider ABC + capability routing), ADR-0008 §3 (secrets via env only), ADR-0009 (bounded-loop cost accounting — CLI/subscription cost = $0.00 convention), ADR-0039 (Tauri shell), R3 (claude-agent-sdk)
- Resolves: "use my Claude subscription" — run the CLI backend (`CliAgentProvider`) on a Claude Pro/Max subscription instead of a pay-per-token `ANTHROPIC_API_KEY`, including **inside the Docker container**.

## Context

The F17 **CLI provider** (`CliAgentProvider`, `backend/app/ingest/provider/cli.py`) delegates the
whole ingest (and read-only chat) to a `claude-agent-sdk` agent (ADR-0007 §3, Karpathy lineage).
Its cost accounting (ADR-0009, as amended by NB-4) already treats **subscription / OAuth auth** as
a first-class case: when the SDK reports no billable cost, the run records `total_cost_usd = $0.00`
because the marginal cost of a Pro/Max subscription genuinely is $0.

But two hard checks made subscription-only operation **impossible**: both `chat()` and
`delegate_ingest()` opened with

```python
api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    raise ValueError("ANTHROPIC_API_KEY not set ...")
```

So the provider demanded an API key (pay-per-token) even though the rest of the module was built to
run at $0 on a subscription. This ADR removes that contradiction.

Two facts frame the decision (verified against the official Claude Code / Agent SDK docs):

1. **Subscription auth is the OAuth path, not the API-key path.** A Pro/Max login produces OAuth
   credentials; a bare `ANTHROPIC_API_KEY` is per-token billing. The documented credential
   **precedence** puts `ANTHROPIC_API_KEY` *above* the subscription — so if it is set (even to an
   empty string), it wins and forces per-token billing. It must be **unset** for a subscription to
   take effect.
2. **The subscription works in a container via a long-lived token.** `claude setup-token` (run once
   on a logged-in host) prints a 1-year OAuth token (`sk-ant-oat01-…`). Passing it into the
   container as **`CLAUDE_CODE_OAUTH_TOKEN`** lets the SDK's bundled `claude` CLI authenticate via
   the subscription with no API key. (The Python `claude-agent-sdk` wheel bundles the CLI binary —
   no Node.js install needed in the image.)

## Decision

### 1. Auth-gate: one resolver, two modes, three signals (I6, §12)

A single module-level helper `_resolve_cli_auth_mode() -> str` replaces both inline
`if not api_key: raise` blocks. It reads only the environment (never mutates `os.environ`), detects
only the **presence** of a token (never reads or forwards a token value), and resolves with this
precedence — mirroring the documented Claude Code credential order:

1. `ANTHROPIC_API_KEY` set & non-empty → **`"api-key"`** (billed per token; real cost recorded).
2. `CLAUDE_CODE_OAUTH_TOKEN` set & non-empty → **`"subscription"`** (container-friendly long-lived
   token from `claude setup-token`; $0 marginal cost).
3. `CLAUDE_CODE_USE_SUBSCRIPTION` truthy (`1/true/yes/on`, case-insensitive) → **`"subscription"`**
   (ambient host login / mounted `~/.claude` creds; $0).
4. none of the above → **`raise ValueError`** naming all three options with actionable guidance.

An **empty** `ANTHROPIC_API_KEY` is treated as *unset* so a stray `export ANTHROPIC_API_KEY=""`
cannot silently outrank the subscription and bill. The resolver is called **before** any SDK
stream/loop opens, so a misconfiguration is a clean pre-stream/pre-loop error, never a fake or
half-open stream (Do-NOT #9).

Signals (2) and (3) both resolve to `"subscription"`: there is no behavioural difference in the
provider — the SDK's spawned `claude` CLI picks the token up from the inherited process environment;
the provider only detects presence to pass the gate. **No token value is ever forwarded into
`ClaudeAgentOptions`** (§12 — secrets stay in the environment, out of app code and DB).

### 2. Cost accounting unchanged; log level softened (I7, ADR-0009)

The ADR-0009 $0.00 convention is unchanged: SDK-reported cost `> 0` (API-key billing) is recorded
truthfully; otherwise `total_cost_usd = $0.00`. The only change is the **log level** — when
`auth_mode == "subscription"` the $0 record logs at **INFO** ("as intended") instead of WARNING, so
an expected-and-correct subscription run does not read as an anomaly. Token counts are still recorded
when the SDK exposes them (WARNING + `tokens=0` if not).

### 3. Packaging (F17, ADR-0039 neighbours)

- `claude-agent-sdk` moves from a lazy-only reference to a real backend dependency
  (`pyproject.toml` core `dependencies`, `>=0.2,<0.3`). The lazy import in `cli.py` stays defensive
  so a missing SDK is a clean pre-stream error.
- `backend/Dockerfile` provides a **writable config dir** for the non-root `synapse` user:
  `/home/synapse/.claude` (mode 700) with `ENV CLAUDE_CONFIG_DIR=/home/synapse/.claude`, so the SDK
  can cache credentials. No Node.js layer — the wheel bundles the CLI binary. No secret is baked in.
- `docker-compose.yml` / `docker-compose.dev.yml` pass `CLAUDE_CODE_OAUTH_TOKEN` and
  `CLAUDE_CODE_USE_SUBSCRIPTION` through from `.env` (alongside the existing `ANTHROPIC_API_KEY`),
  each `${VAR:-}` so nothing is required and no real value is committed. `.env.example` documents the
  three mutually exclusive auth methods, `claude setup-token`, and the **unset `ANTHROPIC_API_KEY`**
  rule.

## Consequences

- ✅ The user can drive ingest **and** chat on the CLI provider with a Pro/Max subscription at $0
  marginal cost — **fully containerized** (`CLAUDE_CODE_OAUTH_TOKEN`) or on a host login
  (`CLAUDE_CODE_USE_SUBSCRIPTION`). Tauri/PWA/browser are unaffected: they remain thin HTTP clients
  of the backend, whichever auth mode it runs.
- ✅ Backward compatible: the API-key path is byte-for-byte unchanged; existing deployments that set
  `ANTHROPIC_API_KEY` behave exactly as before (and, per the precedence, keep winning).
- ⚠️ Operators must remember the precedence trap: to use the subscription, `ANTHROPIC_API_KEY` must
  be **absent**, not merely empty in intent — this is called out in `.env.example`, the compose
  comments, and the v0.6 runbook.
- ⚠️ The `CLAUDE_CODE_OAUTH_TOKEN` is a bearer credential with a ~1-year lifetime. It lives only in
  `.env` (gitignored) and the process environment; it is never logged, never stored in Postgres, and
  never returned by any endpoint. Rotation = re-run `claude setup-token`.
- ⚠️ `claude-agent-sdk` is now a hard backend dependency (heavier install). The infra-free unit tests
  do not import it (the lazy import + auth-gate tests monkeypatch the environment only).

## Do-NOT

1. Do **not** hardcode a provider or model — routing still flows through `capabilities()` and
   `provider_config` (I6); this ADR only changes *auth*, not selection.
2. Do **not** read or forward a token **value** in app code — detect presence only; the SDK's CLI
   reads it from the environment (§12).
3. Do **not** mutate `os.environ` in the resolver.
4. Do **not** treat an empty `ANTHROPIC_API_KEY` as "set" — that would silently bill on a
   subscription-intended run.
5. Do **not** open an SDK stream/loop before the auth gate — a misconfig must fail pre-stream, never
   as a half-open stream.
6. Do **not** bake any token into the Docker image or commit a real value to `.env`/compose.
7. Do **not** change the ADR-0009 cost math — only the subscription log level (WARNING → INFO).
