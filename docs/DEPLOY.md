# Synapse Deployment Guide

<!-- Updated: v2.1.0 | 2026-07-19 -->

> Target: TrueNAS SCALE 25.10 "Goldeye" + Docker Compose (backend) + PWA or Tauri v2 desktop (client)
> Version: v2.1.0
> Status: CURRENT — updated for 2.1.0 release
>
> **2.1.0 note:** No schema migrations, no deploy-relevant or breaking changes since 2.0.0.
> The flagship of this release is the iOS app redesign (see `docs/USER.md §17`), which doesn't
> touch the server deployment described below.
>
> **2.0.0 breaking changes for operators** (still apply if you're upgrading from before 2.0.0):
>
> 1. **`ingest_pipeline_format` env var removed.** If you had this set to `"json"` as a rollback
>    lever, remove the env var. There is only one pipeline now (block-based, ADR-0076). The JSON
>    generation path (`app.ingest.loop`) is deleted.
>
> 2. **`orchestrator` import aliases removed.** If you have internal scripts or integrations that
>    `import app.ingest.orchestrator` for `IngestResult`, `run_ingest_pipeline`, or `write_wiki_page`,
>    update them: `IngestResult` and `run_ingest_pipeline` are in `app.ingest.pipeline`;
>    `write_wiki_page` is in `app.ingest.writer`.
>
> 3. **HTTP error body changed.** Every error response now uses
>    `{"error": {"code": "<slug>", "message": "...", "status": N, "details": null|[...]}}`.
>    The old `{"detail": "..."}` shape is gone (ADR-0086). Update any monitoring scripts or
>    client code that parsed `response.detail` to read `response.error.message` instead.

---

## 1. Prerequisites

### 1.1 External services (already running on TrueNAS — do not containerize)

Synapse reuses the services you already run. These are NOT defined as Docker Compose
services; they are referenced by environment variable only.

| Service | Purpose | Default port | Notes |
|---------|---------|--------------|-------|
| **Ollama** | Local LLM inference (RTX 3060) and bge-m3 embeddings | 11434 | GPU required for non-trivial models; bge-m3 runs via `/api/embeddings` |
| **Qdrant** | Vector store for bge-m3 embeddings | 6333 | Data persists on TrueNAS storage; shared across vaults |
| **SearXNG** | Web search backend (required for Deep Research, M5+) | 8888 | Optional for v0.4 |

Verify each service is reachable before starting Synapse:

```bash
# From TrueNAS or any Tailscale node — replace 100.x.x.x with the actual Tailscale IP
curl -s http://100.x.x.x:11434/api/tags | jq '.models'   # Ollama — lists loaded models
curl -s http://100.x.x.x:6333/health                      # Qdrant — returns {"result":"ok"}
```

### 1.2 Docker and Docker Compose

TrueNAS SCALE 25.10 ships Docker support via the Apps subsystem. Confirm both tools
are present:

```bash
docker --version
docker compose version
```

### 1.3 Client options (PWA or Tauri v2 desktop)

Synapse v0.6 ships the same React frontend in two distribution formats:

| Client | Installation | Offline | Native UI | Best for |
|--------|--------------|---------|-----------|----------|
| **PWA (browser)** | `https://your-domain/` (visit in any browser; "Install" via browser menu) | Yes (service worker cache) | Browser chrome | Quick start, mobile, multi-device |
| **Tauri v2 desktop** | Download `.deb`/`.AppImage` (Linux), `.dmg` (macOS), `.msi`/`.nsis` (Windows) from GitHub releases | Yes (same service worker) | Native (WebKit) | Single-machine, native OS integration, offline-first |

Both share the same backend API (`http://backend:8000`). Choose based on your preference:

- **For single-user/homelab deployments:** Download the Tauri desktop binary for your OS or run the PWA in a pinned browser window.
- **For multi-user deployments:** Recommend the PWA for accessibility; Tauri for power users preferring native apps.

**ADR-0039** (Tauri v2 desktop shell) documents the implementation. See §7 for desktop app usage.

### 1.4 Vault storage paths

Create the following dataset paths on your TrueNAS storage pool before the first run.
The exact pool name is site-specific; substitute your own:

```
/mnt/pool/synapse/
├── vault/
│   ├── raw/
│   │   └── sources/          ← drop documents here to trigger ingest
│   ├── wiki/                 ← Synapse writes wiki pages here (valid Obsidian vault)
│   ├── schema.md             ← vault rules (frontmatter requirements, wikilink style)
│   └── purpose.md            ← vault goal and key questions (injected as context)
└── postgres-backups/         ← daily dump target (optional; see §5)
```

```bash
# SSH into TrueNAS, then:
zfs create pool/synapse
zfs create pool/synapse/vault
zfs create pool/synapse/postgres-backups
chmod 755 /mnt/pool/synapse /mnt/pool/synapse/vault
```

---

## 2. Environment configuration

Copy `.env.example` to `.env` and edit it for your environment. Never commit `.env`.

```bash
cp .env.example .env
```

### 2.1 Full variable reference

| Variable | Example value | Required | Notes |
|----------|--------------|----------|-------|
| `DATABASE_URL` | `postgresql+asyncpg://synapse:synapse@postgres:5432/synapse` | Yes | Docker: use `postgres` as the host. Local dev: `localhost`. |
| `QDRANT_URL` | `http://host.docker.internal:6333` | Yes | Docker: `host.docker.internal`. Tailscale: `http://100.x.x.x:6333`. |
| `QDRANT_COLLECTION` | `synapse_pages` | Yes | Qdrant collection name; created on first run if absent. |
| `EMBEDDING_URL` | `http://host.docker.internal:11434/api/embeddings` | Yes | bge-m3 via Ollama. Same host as `OLLAMA_URL`. |
| `EMBEDDING_MODEL` | `bge-m3` | Yes | Model name as registered in Ollama. |
| `EMBEDDING_DIM` | `1024` | Yes | Must match the actual bge-m3 output dimension. Verify with the curl command in §6.2. |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Yes | Base URL for the generative Ollama instance (used by OllamaProvider for ingest/chat). |
| `VAULT_ID` | `default` | Yes | Logical vault identifier. Supports multiple vaults in future sprints. |
| `VAULT_PATH` | `/vault` | Yes | Container path. Bind-mounted from host in docker-compose.yml. Local dev: `../vault` or an absolute path. |
| `CORS_ALLOW_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Yes | Comma-separated list of browser origins allowed to call the API. Set to your PWA/Tauri origin in production. The Vite dev server default (`localhost:5173`) is fine for local development. |
| `DEFAULT_MODEL_ID` | `claude-sonnet-4-6` | No | Used only by the Alembic data migration (0002) to seed the initial `provider_config` row. Not read by the application at runtime. |
| `MAX_UPLOAD_BYTES` | `26214400` | No | Maximum file size (bytes) for `POST /ingest/upload`. Default 25 MB. Files over this limit receive 413. (ADR-0020 §2.4, I7) |
| `IMPORT_SCAN_MAX_FILES` | `200` | No | Maximum number of files copied per scheduled scan tick. Remaining files are picked up on the next tick. (ADR-0020 §4.4, I7) |
| `IMPORT_SCAN_MAX_SECONDS` | `60` | No | Wall-clock cap (seconds) per scheduled scan tick. Scan stops early if exceeded; continues next tick. (ADR-0020 §4.4, I7) |
| `IMPORT_SCAN_RECURSIVE` | `false` | No | When `true`, the scheduled folder import traverses subdirectories recursively, preserving the path structure inside `vault/raw/sources/`. A `folderContext` hint (joined subdirectory segments, e.g. `reports/2026/q2`) is injected into the ingest analysis prompt for each file. Default `false` (non-recursive, v0.6 behavior). (G-P1-9) |
| `EMBEDDINGS_ENABLED` | `true` | No | Set to `false` to disable bge-m3 vectorization and Qdrant entirely. Ingest still runs (Postgres metadata + links only); retrieval and `/search` degrade to lexical Postgres keyword search. Startup skips the embedding probe — Synapse starts even when Qdrant and Ollama embeddings are unreachable. (ADR-0030) |
| `EMBEDDING_FORMAT` | `ollama` | No | Request/response adapter for the embedding service: `ollama` (default — `{"prompt": ...}` → `{"embedding": [...]}`) or `openai` (`{"input": ...}` → `{"data":[{"embedding":[...]}]}`). Set to `openai` when `EMBEDDING_URL` points at an OpenAI-compatible endpoint (e.g. a hosted embeddings API). (ADR-0031) |
| `EMBEDDING_API_KEY` | *(none)* | No | Bearer token for the embedding service. When set, every embedding request includes `Authorization: Bearer <key>`. Leave unset for the local bge-m3/Ollama service (no auth). Never logged or returned by any endpoint. (ADR-0031) |
| `MCP_AUTH_TOKEN` | *(none)* | No | Static bearer token for the remote MCP HTTP surface at `/mcp/server`. When set, the HTTP surface is mounted and requires `Authorization: Bearer <token>` on every request. When **unset**, `/mcp/server` is not mounted (404) — fail-closed. The stdio entry (`python -m app.mcp.server`) is unaffected by this variable. Never logged or returned by any endpoint. (ADR-0029, see §5) |
| `MCP_REMOTE_WRITE_ENABLED` | `false` | No | When `true`, the `write_page` tool is also exposed on the HTTP MCP surface (still bearer-gated by `MCP_AUTH_TOKEN`). Default `false`: only `search_wiki`, `get_page`, `list_pages` are reachable remotely. The stdio path always has all four tools regardless of this setting. (ADR-0029 §2.3) |
| `MCP_TRUSTED_PROXIES` | *(empty)* | No | Comma-separated list of trusted reverse-proxy IP addresses (e.g. `127.0.0.1,::1`). When set, the `X-Forwarded-For` header from listed IPs is honoured to determine the real client IP for the allow-without-token public/private classification. Leave empty (the default) unless you run a local reverse proxy in front of Synapse. The Cloudflare header check (`CF-Connecting-IP`/`CF-Ray`) is independent of this setting. (ADR-0033) |
| `BACKEND_PROXY_TARGET` | `http://localhost:8000` | No | **Dev only (Vite proxy, server-side).** The URL the Vite dev server proxies API calls to. Set to `http://synapse-backend:8000` in `docker-compose.dev.yml` so the Vite process (inside the container) can reach the backend over the Docker network. This variable is intentionally NOT prefixed `VITE_` — it is never inlined into the browser bundle. Browser clients always use a relative base (`""`) by default. (ADR-0028) |
| `REVIEW_PROPOSE_MIN_CHARS` | `10000` | No | Anti-spam gate (ADR-0034 §4.2): the proposal LLM call runs only if the total written content from an ingest run is at least this many characters (one of several OR'd gate conditions). Below the gate (and absent other signals) → zero proposals, zero LLM cost. (I7) |
| `REVIEW_PROPOSE_MIN_PAGES` | `1` | No | Anti-spam gate (ADR-0034 §4.2): the proposal LLM call runs if at least this many pages were written in the run (OR'd with the char / dangling-link / suggested-page conditions). v1.5.2 lowered 4 → 1 so reviews are proposed on ordinary single-page ingests (llm_wiki volume parity). (I7) |
| `REVIEW_PROPOSE_MAX_ITEMS` | `12` | No | Hard cap on proposals emitted per ingest run (ADR-0034 §4.3). The single LLM proposal call's output is truncated to this count — never an unbounded enqueue. v1.5.2 raised 8 → 12. (I7) |
| `REVIEW_PROPOSE_TOKEN_BUDGET` | `4000` | No | Fallback token budget for the single proposal provider call (ADR-0034 §4.3). Used when the resolved `provider_config` row carries no budget. Small: a compact analysis digest plus up to 8 proposals fits comfortably. (I7) |
| `REVIEW_PROPOSE_TIMEOUT_SECONDS` | `30.0` | No | Timeout wrapping the single proposal provider call (ADR-0034 §4.3). On timeout → emit only rule-based proposals (degrade safely; never fail ingest). (I7) |
| `REVIEW_SWEEP_MAX_ITEMS` | `200` | No | Cap on the number of pending `missing-page`/`duplicate` items processed by the rule-based sweep pass per run (ADR-0034 §6.2). Bounded indexed read; no vault re-scan (I1/I7). |
| `REVIEW_SWEEP_LLM_ENABLED` | `true` | No | Gate for the sweep Pass-2 conservative LLM judgment (ADR-0034 §6.3). Default on (a single bounded call). Set `false` for zero-cost operation: Pass-1 still runs; Pass-2 returns keep-all. (I7) |
| `REVIEW_SWEEP_LLM_MAX_ITEMS` | `8` | No | Cap on the number of candidate items batched into the single sweep Pass-2 LLM call (ADR-0034 §6.3). Items beyond the cap remain pending until the next sweep. (I7) |
| `REVIEW_SWEEP_LLM_TOKEN_BUDGET` | `4000` | No | Fallback token budget for the single sweep Pass-2 provider call (ADR-0034 §6.3). Used when the resolved `provider_config` row carries no budget. (I7) |
| `REVIEW_SWEEP_TIMEOUT_SECONDS` | `30.0` | No | Timeout wrapping the sweep Pass-2 provider call (ADR-0034 §6.3). On timeout or any ambiguity → keep ALL items pending (default-to-keep bias). `confirm` items are never auto-resolved regardless. (I7) |
| `LINT_MAX_ITER` | `3` | No | Iteration cap for the bounded lint scan loop (K2, ADR-0037 §4, I7). The loop stops after this many passes regardless of remaining token budget. Caller-overridable (1–10) via `POST /lint/scan`; the value is frozen on the `lint_runs` row at INSERT and never re-read mid-loop. |
| `LINT_TOKEN_BUDGET` | `20000` | No | Token budget for one lint scan run (K2, ADR-0037 §4, I7). Semantic provider calls stop when `total_tokens >= LINT_TOKEN_BUDGET`. Caller-overridable (1 000–1 000 000) via `POST /lint/scan`; frozen on the `lint_runs` row at INSERT. |
| `LINT_MAX_FINDINGS` | `50` | No | Hard cap on findings emitted per lint run (K2, ADR-0037 §4, I7). Deterministic and semantic findings are merged and truncated to this count — never an unbounded enqueue. |
| `LINT_TIMEOUT_SECONDS` | `30.0` | No | Timeout (seconds) wrapping each semantic lint provider call (K2, ADR-0037 §4, I7). On timeout → emit only the deterministic (orphan/structural) findings and degrade gracefully; the scan never fails hard. |
| `CLIP_ENABLED` | `false` | No | Master gate for the `POST /clip` ingress endpoint (F11, ADR-0038). Default `false` — must be explicitly set to `true` to open the web-clipper ingress. When `false`, `POST /clip` returns 503. Setting to `true` still requires `CLIP_TOKEN` to be set; an enabled endpoint with no token rejects all requests with 401. |
| `CLIP_TOKEN` | *(none)* | No | SECRET. Bearer token required on every `POST /clip` request (F11, ADR-0038 §2.1). Compared constant-time (`hmac.compare_digest`). Missing or invalid token → 401. Generate with `openssl rand -base64 32`. Never logged or returned by any endpoint. Set to a high-entropy random string. |
| `CLIP_ALLOWED_ORIGINS` | *(empty)* | No | Comma-separated allowlist of permitted request Origins for `POST /clip` (F11, ADR-0038 §2.2). Each entry is an exact origin string (scheme+host, no path or query). Example: `chrome-extension://abcdefghijklmnopqrstuvwxyz,http://127.0.0.1:5173`. An empty string allows only loopback/localhost requests (implicit). Add your Chrome extension's origin ID when deploying the web clipper. |
| `CLIP_MAX_BODY_BYTES` | `2097152` | No | Maximum allowed body size for `POST /clip` (F11, ADR-0038 §2.3, I7). Default 2 MB — generous for any realistic Markdown clip. Requests with a body exceeding this limit receive 413. |
| `PDF_EXTRACTOR` | `pypdf` | No | PDF text extraction backend: `pypdf` (default, pure Python, no extra services) or `marker` (high-quality vision-model pipeline via the Marker microservice, ADR-0051, R8-1). When `marker` is set, `extract_text()` POSTs the raw PDF bytes to `MARKER_SERVICE_URL` with a bounded timeout. On any failure the backend logs a WARNING and falls back to pypdf — pypdf is never removed. |
| `MARKER_SERVICE_URL` | `http://host.docker.internal:8555` | No | Base URL for the Marker extraction microservice (`tools/marker-converter/service.py`). Only read when `PDF_EXTRACTOR=marker`. The service exposes `POST /convert` (multipart PDF → `{"markdown", "pages"}`) and `GET /health`. See `tools/marker-converter/README.md` for setup. |
| `MARKER_TIMEOUT_SECONDS` | `120` | No | Timeout (seconds) for a single Marker `/convert` HTTP call (ADR-0051, I7). On timeout the backend falls back to pypdf. Default 120 s — generous for large scanned PDFs on GPU. |
| `VISION_CAPTIONS_ENABLED` | `false` | No | When `true`, Synapse generates an AI caption for each ingested image file (`.png`/`.jpg`/`.jpeg`/`.webp`) via the active provider's `chat()` method (requires `supports_vision=true` in provider capabilities). Captions are cached in the `image_captions` table keyed by SHA-256 of the file bytes — the same image is never captioned twice (R8-2, G-P2-1, I7). Default `false` — images produce a stub placeholder without this flag. |
| `VISION_MAX_IMAGES_PER_RUN` | `10` | No | Maximum number of image files captioned per ingest trigger when `VISION_CAPTIONS_ENABLED=true` (I7 cap). Files beyond the cap are deferred to the next run. |
| `AV_TRANSCRIPTION_ENABLED` | `false` | No | When `true`, Synapse transcribes audio and video files (`.mp3`/`.m4a`/`.wav`/`.mp4`/`.mov`/`.webm`) using the host Whisper microservice at `WHISPER_SERVICE_URL` before ingest (R8-3). Default `false` — AV files produce a stub placeholder without this flag. |
| `WHISPER_SERVICE_URL` | `http://host.docker.internal:8556` | No | Base URL for the Whisper transcription microservice (`tools/whisper-service/`). Only read when `AV_TRANSCRIPTION_ENABLED=true`. The service exposes `POST /transcribe` (multipart audio/video → `{"text"}`) and `GET /health`. See `tools/whisper-service/README.md` for setup. |
| `AV_MAX_FILES_PER_RUN` | `10` | No | Maximum number of audio/video files transcribed per ingest trigger when `AV_TRANSCRIPTION_ENABLED=true` (I7 cap). Files beyond the cap are deferred to the next run. |
| `COST_ALERT_THRESHOLD_USD` | *(none)* | No | When set to a positive decimal (e.g. `5.00`), the Settings > Costi dashboard shows a red alert indicator when the month-to-date total cost across all providers and operations exceeds this value. The indicator is informational only — no AI calls are blocked or rate-limited when the threshold is exceeded. Default unset (alert always off). (R9-1, I7) |
| `PURPOSE_SUGGESTION_ENABLED` | `true` | No | When `true`, the ingest orchestrator emits a `purpose-suggestion` ReviewItem after each orchestrated ingest run when scope-drift signals are detected. Default `true` — opt out with `false` for zero-LLM-cost operation. The proposal fires through the same anti-spam gate as other review proposals. (R9-3, F2, ADR-0034 §4.2) |
| `PURPOSE_SUGGESTION_MAX_TOKENS` | `2000` | No | Token budget for the single bounded provider call that evaluates scope drift and drafts the purpose suggestion rationale. Small: only the analysis digest and current `purpose.md` are included. (R9-3, I7) |
| `PURPOSE_SUGGESTION_MIN_SOURCES` | `3` | No | Minimum number of sources in the current ingest run before the purpose-suggestion gate is considered. Below this threshold the call is suppressed (no cost). (R9-3, I7 anti-spam gate) |
| `PURPOSE_SUGGESTION_TIMEOUT_SECONDS` | `20.0` | No | Timeout (seconds) wrapping the purpose-suggestion provider call. On timeout → no suggestion emitted; ingest is unaffected. (R9-3, I7) |
| `SCHEMA_SUGGESTION_ENABLED` | `false` | No | **Default off.** When `true`, the ingest orchestrator emits a `schema-suggestion` ReviewItem when emerging frontmatter patterns in recent pages deviate from `schema.md` rules. Disabled by default because an unreviewed schema change has a wide blast-radius — every future ingest run and every validation pass is affected. Enable only after reading ADR-0034 §4.2 and after ensuring you have a review process in place for schema proposals. (R9-4, K6, I7) |
| `SCHEMA_SUGGESTION_MAX_TOKENS` | `2000` | No | Token budget for the schema-suggestion provider call. Only read when `SCHEMA_SUGGESTION_ENABLED=true`. (R9-4, I7) |
| `SCHEMA_SUGGESTION_MIN_SOURCES` | `5` | No | Minimum number of sources in the run before the schema-suggestion gate is considered. Higher than the purpose-suggestion threshold because schema proposals require a stronger pattern signal. (R9-4, I7 anti-spam gate) |
| `SCHEMA_SUGGESTION_TIMEOUT_SECONDS` | `20.0` | No | Timeout (seconds) wrapping the schema-suggestion provider call. On timeout → no suggestion emitted. (R9-4, I7) |
| `GRAPH_COHESION_WARN` | `0.15` | No | Cohesion score threshold below which a community is flagged with a warning indicator in the graph community panel (`GET /graph/communities/{id}`). Default `0.15` (mirrors the llm_wiki threshold). Communities with `cohesion < GRAPH_COHESION_WARN` are marked visually in the drill-down panel. (R9-5) |
| `SYNAPSE_DEPLOYMENT_MODE` | `local` | No | Deployment trust boundary (ADR-0075): `local` preserves backward-compatible zero-config use; `server` declares a network-reachable process and fails startup unless `SYNAPSE_AUTH_TOKEN` passes validation. Use `server` for LAN, Tailscale, tunnel, reverse-proxy and hosted deployments. Accepted values are exactly `local` and `server`. |
| `SYNAPSE_BIND_HOST` | `127.0.0.1` | No | Host address used by Docker Compose to publish port 8000. Keep loopback in `local` mode. Set `0.0.0.0` only together with `SYNAPSE_DEPLOYMENT_MODE=server` and a strong token. |
| `SYNAPSE_AUTH_TOKEN` | *(empty)* | Required in `server` mode | Env-only shared Bearer token for the REST API (ADR-0052/0075). Empty or absent disables API auth only in `local` mode. `server` mode requires at least 32 characters, no whitespace and at least eight distinct characters; generate with `openssl rand -hex 32`. Protected routes require `Authorization: Bearer <token>` and use constant-time comparison. Public health is limited to `GET`/`HEAD /status` and `/health/live`; `/health/detailed` is protected. API docs, CORS preflight, `/mcp/server/*` and `POST /clip` retain their documented exemptions/independent credentials. Never store, hash, log or commit this token. |
| `POSTGRES_USER` | `synapse` | No | PostgreSQL database user. Drives the postgres container's `POSTGRES_USER` environment variable and, when `DATABASE_URL` is left at its default, must match the credential encoded in `DATABASE_URL`. Override via `.env`; never commit the actual value for production deployments. Default `synapse` (matches the compose default). (R13-9) |
| `POSTGRES_PASSWORD` | `synapse` | No | PostgreSQL user password. Same scoping as `POSTGRES_USER`. Override in `.env` with a strong random value for production: `openssl rand -base64 24`. If you change this, also update `DATABASE_URL` (or its components). Default `synapse` (acceptable only for local/dev; not for internet-facing deployments). (R13-9) |
| `POSTGRES_DB` | `synapse` | No | PostgreSQL database name. Drives the postgres container's `POSTGRES_DB` environment variable. Default `synapse`. (R13-9) |
| `RATE_LIMIT_ENABLED` | `true` | No | Enable the in-process per-IP fixed-window rate limiter on inference-cost endpoints (`POST /chat/stream`, `/ingest/trigger`, `/ingest/upload`, `/ingest/from-text`, `/research/start`). Set `false` in CI or dev to remove the 429 gate. Default `true`. (R13-9, B4) |
| `RATE_LIMIT_REQUESTS` | `20` | No | Maximum requests per IP per window for the endpoints listed above. Requests beyond this limit receive HTTP 429 with a `Retry-After` header. Default 20. (R13-9, B4) |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | No | Window length in seconds for the per-IP rate limiter. The counter resets to zero at the start of each new window. Default 60 s. (R13-9, B4) |
| `BACKUP_DIR` | *(unset)* | No | Directory the scheduled `pg_dump` archives are written to (1.9.1 W4, SEC-OPS-2). Unset resolves to `<VAULT_PATH>/.synapse/backups`. See §12.0. |
| `BACKUP_RETENTION_COUNT` | `7` | No | Number of most-recent backup archives kept per vault; older ones are deleted after each successful run (I7 — bounded disk growth). (SEC-OPS-2) |
| `BACKUP_TIMEOUT_SECONDS` | `300` | No | Hard wall-clock timeout (seconds) for the `pg_dump` subprocess. A stuck dump is killed rather than hanging the scheduler or `POST /ops/system-update`. (SEC-OPS-2, I7) |
| `PG_DUMP_PATH` | `pg_dump` | No | Executable name/path for the `pg_dump` client binary. Default assumes it's on `PATH` (the shipped image installs `postgresql-client`). (SEC-OPS-2) |

### 2.2 Example .env for TrueNAS Docker deployment

```env
DATABASE_URL=postgresql+asyncpg://synapse:synapse@postgres:5432/synapse

QDRANT_URL=http://host.docker.internal:6333
QDRANT_COLLECTION=synapse_pages

EMBEDDING_URL=http://host.docker.internal:11434/api/embeddings
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024

OLLAMA_URL=http://host.docker.internal:11434

VAULT_ID=default
VAULT_PATH=/vault

CORS_ALLOW_ORIGINS=http://truenas.local:5173,http://localhost:5173
```

### 2.3 Example .env for local development (macOS/Linux with Tailscale)

```env
DATABASE_URL=postgresql+asyncpg://synapse:synapse@localhost:5432/synapse

QDRANT_URL=http://100.x.x.x:6333
QDRANT_COLLECTION=synapse_pages

EMBEDDING_URL=http://100.x.x.x:11434/api/embeddings
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIM=1024

OLLAMA_URL=http://100.x.x.x:11434

VAULT_ID=default
VAULT_PATH=../vault

CORS_ALLOW_ORIGINS=http://localhost:5173
```

---

## 3. First-run startup

### 3.1 Services defined in docker-compose.yml

The Compose file defines exactly two services:

| Service | Image / build | Purpose |
|---------|--------------|---------|
| `postgres` | `postgres:16-alpine` | PostgreSQL 16 — metadata, links, graph coords, provider config, conversations |
| `synapse-backend` | `./backend/Dockerfile` (local build) | FastAPI app — watcher, ingest, REST API, graph engine, chat |

Ollama, Qdrant, SearXNG, and bge-m3 are external; they are referenced via env vars and
accessed from the containers as `host.docker.internal:<port>`.

### 3.2 Start the stack

```bash
# From the repo root
docker compose up -d

# Follow backend logs to watch the first startup
docker compose logs -f synapse-backend
```

On the first startup the backend runs:

```
alembic upgrade head
```

before launching uvicorn. This creates all tables (migrations 0001–0010), seeds the
`vault_state` row, and inserts the initial `provider_config` rows using the
`DEFAULT_MODEL_ID` env var. You do not need to run migrations manually.

Migration 0008 creates the `import_schedules` table (Feature S, ADR-0020 §4.1).
Migration 0009 creates `deep_research_runs` and `deep_research_sources` (F10, ADR-0024).
Migration 0010 creates `review_items` (F9, ADR-0025).
Migration 0011 adds `vault_state.remote_mcp_enabled` (ADR-0032 §3).
Migration 0012 adds `vault_state.mcp_access_token_hash` and `vault_state.mcp_allow_without_token`
(ADR-0033 §2.1/§2.3 — UI-settable token as salted PBKDF2 hash; allow-without-token flag).
Migration 0013 rewrites `review_items` for the ADR-0034 proposal model: adds six new columns
(`source_page_id`, `proposed_title`, `proposed_page_type`, `proposed_dir`, `rationale`,
`resolution`, `created_page_id`), drops `pre_generated_query`, extends `status` with the new
lifecycle values (`created`, `auto_resolved`), and left-shifts any legacy `new_page`/`approved`
rows to `skipped` (they reference auto-created pages that already exist and are obsolete under
the proposal model). Adds index `ix_review_items_vault_proposed_title` for the rule-based sweep.
Migration 0014 adds the `lint_runs` and `lint_findings` tables (K2, ADR-0037): `lint_runs` tracks
each bounded lint scan run (vault_id, status, max_iter_used, total_tokens, total_cost_usd,
converged, started_at, completed_at); `lint_findings` stores per-finding rows (run_id FK,
category, page_id FK, severity, message, suggested_fix, acknowledged, applied, applied_at).
All tables are empty on first run and are populated through normal use.

### 3.3 Verify the backend is up

```bash
curl http://localhost:8000/status
```

Expected response:

```json
{
  "vault_id": "default",
  "data_version": 0,
  "started_at": "2026-06-28T10:30:00Z",
  "uptime_seconds": 42
}
```

### 3.4 Liveness and detailed health checks

Use the public, non-diagnostic endpoint for container and uptime liveness probes:

```bash
curl -s http://localhost:8000/health/live
# {"status":"ok"}
```

`GET /health/live` intentionally returns no component, vault, configuration, dependency or error
details. `GET /status` also remains public for the desktop connection contract.

For an operator dashboard, call the richer endpoint with the REST bearer token:

```bash
curl -s \
  -H "Authorization: Bearer <your-token>" \
  http://localhost:8000/health/detailed | jq .
```

The response lists the status of each internal component:

```json
{
  "status": "healthy",
  "version": "0.9.0",
  "components": {
    "postgres":       { "status": "ok" },
    "qdrant":         { "status": "ok" },
    "ollama":         { "status": "ok" },
    "watcher":        { "status": "ok", "last_event_seconds_ago": 12 },
    "scheduler":      { "status": "ok", "next_run_in_seconds": 3588 },
    "ingest_queue":   { "status": "idle", "running": 0, "queued": 0 }
  }
}
```

`status` at the top level is `"healthy"` if all components are `"ok"` or `"idle"`.
It is `"degraded"` if any non-critical component has a transient issue (e.g. Qdrant
temporarily unreachable when embeddings are enabled), and `"unhealthy"` if Postgres is
unreachable. Use `/health/live` for unauthenticated Docker health checks and black-box uptime
monitors. Use `/health/detailed` only when the monitoring client can send the bearer credential.
When auth is enabled, a detailed request without that credential receives 401.

The basic `GET /status` endpoint (vault_id, data_version, uptime) remains available for
the desktop app's Connect-screen probe and is not affected by this change.

### 3.5 Open the frontend

The frontend is served by the Vite dev server (development) or a static file server
(production build). In v0.4 development mode:

```bash
cd frontend
npm install
npm run dev
```

Navigate to `http://localhost:5173`. The three-panel shell should load with the
navigation rail on the left and the knowledge graph in the center.

### 3.6 Network posture and hardening (R13-9)

Synapse is designed for **LAN / Tailscale / Cloudflare-Tunnel** deployments. It is
**not designed for raw public internet exposure**. Key assumptions:

- The TrueNAS host is on a trusted home LAN (or accessible only via Tailscale mesh).
- External access is proxied through Cloudflare Tunnel (which enforces TLS and,
  optionally, Cloudflare Access authentication) — never via a raw-WAN open port.
- Every backend reachable beyond the same machine runs with
  `SYNAPSE_DEPLOYMENT_MODE=server`; startup then requires application-level bearer auth even when
  edge authentication is also present (ADR-0075, defence in depth).

**Postgres network isolation.** In `docker-compose.yml` (production), the Postgres port
**5432 is not published to the host interface**. Postgres is reachable only by the
`synapse-backend` container on the compose-internal Docker bridge network. To connect
interactively from the host for debugging, use `docker compose exec postgres psql -U synapse`
or temporarily uncomment the loopback-only port line in `docker-compose.yml`:

```yaml
# ports:
#   - "127.0.0.1:5432:5432"
```

In `docker-compose.dev.yml` (local development), Postgres and Qdrant are published on
`127.0.0.1` only — accessible from the developer's machine but not the LAN.

**Postgres credentials.** Default credentials (`synapse`/`synapse`) are acceptable for
local development only. For any internet-adjacent or shared deployment, override in `.env`:

```env
POSTGRES_USER=synapse
POSTGRES_PASSWORD=<strong random password — openssl rand -base64 24>
POSTGRES_DB=synapse
# Update DATABASE_URL to match:
DATABASE_URL=postgresql+asyncpg://synapse:<password>@postgres:5432/synapse
```

The compose file uses `${POSTGRES_USER:-synapse}` / `${POSTGRES_PASSWORD:-synapse}` so
`docker compose up` works out of the box without an `.env` file, while a production
`.env` overrides the defaults silently.

**Inference-cost rate limiting.** Endpoints that trigger AI inference
(`POST /chat/stream`, `/ingest/trigger`, `/ingest/upload`, `/ingest/from-text`,
`/research/start`) carry an in-process fixed-window rate limiter (per client IP,
configurable, no external dependency). Excess requests receive HTTP 429 with a
`Retry-After` header. Streaming request **starts** are counted, not individual tokens (I3).
Configurable via `RATE_LIMIT_ENABLED` / `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS`
(see §2.1 for defaults and descriptions).

**SSRF guard for Deep Research.** When Deep Research fetches the URLs returned by
SearXNG, every outbound HTTP request passes through the SSRF guard (`app/security_net.py`).
Blocked ranges: RFC 1918 (10/8, 172.16/12, 192.168/16); loopback (127/8, ::1);
link-local / cloud metadata (169.254/16, incl. 169.254.169.254); IPv6 ULA (fc00::/7),
link-local (fe80::/10), and multicast (ff00::/8). Redirects are capped at 3 hops,
re-validating the target on each hop. The SearXNG search endpoint itself (`SEARXNG_URL`)
is trusted operator config and is NOT subject to the SSRF guard.

**Auth route method-awareness.** The bearer-token auth middleware (`SYNAPSE_AUTH_TOKEN`)
exempts routes by **(path, method) pair**, not path alone. A future mutating route on an
otherwise-probe path (e.g. `POST /status`) will NOT be silently open. (R13-9 / B11)

---

## 4. Configuring an inference provider

Synapse requires at least one `provider_config` row for each operation you want to
use. The Alembic migration seeds a global API provider row using `DEFAULT_MODEL_ID`
at startup. To use a **local Ollama model** for chat (e.g. `qwen2.5:3b`), insert a
row manually or via the Settings UI.

### 4.1 Insert a local provider row via psql

```bash
docker compose exec postgres psql -U synapse -d synapse
```

```sql
-- Insert a global provider_config row for the local Ollama backend.
-- This makes qwen2.5:3b the default for ALL operations globally.
INSERT INTO provider_config (scope, operation, vault_id, provider_type, model_id, max_iter, token_budget, is_fallback)
VALUES ('global', NULL, NULL, 'local', 'qwen2.5:3b', 3, 60000, false);

-- To target only the chat operation for the default vault:
INSERT INTO provider_config (scope, operation, vault_id, provider_type, model_id, max_iter, token_budget, is_fallback)
VALUES ('operation', 'chat', 'default', 'local', 'qwen2.5:3b', 3, 60000, false);
```

Resolution precedence (most specific wins): `operation + vault_id` > `vault` >
`global`. A missing global row is a hard configuration error; Synapse will not fall
back silently to a hardcoded provider.

### 4.2 Configure via the UI

Open the Settings section in the web UI (gear icon at the bottom of the nav rail).
The "Provider configuration" table lists all `provider_config` rows. Use the header
dropdown ("Provider") to select the active provider for the current session. Changes
persist across page reloads.

### 4.3 Use the Anthropic API provider

Set your API key as an environment variable before starting the stack:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up -d
```

The key is never stored in the database. Then insert a provider_config row:

```sql
INSERT INTO provider_config (scope, operation, vault_id, provider_type, model_id, max_iter, token_budget, is_fallback)
VALUES ('global', NULL, NULL, 'api', 'claude-sonnet-4-6', 3, 60000, false);
```

For an OpenAI-compatible endpoint, populate the `base_url` column:

```sql
INSERT INTO provider_config (scope, operation, vault_id, provider_type, model_id, base_url, max_iter, token_budget, is_fallback)
VALUES ('global', NULL, NULL, 'api', 'gpt-4o', 'https://api.openai.com/v1', 3, 60000, false);
```

---

## 5. Remote MCP (Feature A, ADR-0029 / ADR-0032 / ADR-0033)

The Synapse backend exposes a **remote MCP server** over HTTP at the path `/mcp/server`,
secured by a bearer token. This allows tools like
[claude.ai](https://claude.ai/new) to connect to your vault and use the four MCP tools:
`search_wiki`, `get_page`, `list_pages` (read), and optionally `write_page` (mutating).

### 5.1 Prerequisites

- A bearer token must be configured — either via `MCP_AUTH_TOKEN` (bootstrap env var) or
  generated from the Settings → API + MCP panel in the UI (stored hashed; see §5.9).
- The token is stored as a PBKDF2-HMAC-SHA256 salted hash and is **never** logged or
  returned in plaintext after the initial generation (fail-closed security).
- Read-only by default: `write_page` is disabled unless `MCP_REMOTE_WRITE_ENABLED=true`.
- `MCP_AUTH_TOKEN` remains a **bootstrap fallback** for headless/Docker setups. A token
  generated in the UI (stored in the DB) takes precedence over the env var when both are set.

### 5.2 Generate a strong bearer token

```bash
# Generate a cryptographically secure token
MCP_AUTH_TOKEN="$(openssl rand -base64 32)"
export MCP_AUTH_TOKEN
echo "Store this safely: $MCP_AUTH_TOKEN"

# Persist it in .env for local testing (NEVER commit it)
echo "MCP_AUTH_TOKEN=$MCP_AUTH_TOKEN" >> .env
```

### 5.3 Start the stack with the token set

```bash
# Option 1: export the variable in the same shell
export MCP_AUTH_TOKEN="<your-generated-token>"
docker compose up -d

# Option 2: use a .env file (created above)
# The token is sourced from .env and passed to the backend container
docker compose up -d
```

### 5.4 Verify the HTTP MCP surface is live

```bash
# Test that /mcp/server is reachable and requires authentication
curl -s -w "\nStatus: %{http_code}\n" http://localhost:8000/mcp/server \
  | head -20
# Expected: 401 Unauthorized (no token)

# Now with the token
curl -s -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  http://localhost:8000/mcp/server | head -20
# Expected: Valid MCP protocol response (200 or 101 SSE/WebSocket handshake)
```

### 5.5 Expose over Cloudflare Tunnel

If you use **Cloudflare Tunnel** to expose Synapse publicly, the remote MCP surface
rides the same tunnel as the REST API. Add an ingress rule if needed:

```yaml
# Your Cloudflare Tunnel config (~/.warp/config.yaml or TrueNAS UI)
ingress:
  - hostname: synapse.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

The `/mcp/server` path is automatically forwarded under the same hostname:
`https://synapse.yourdomain.com/mcp/server` (bearer token required).

### 5.6 Add the remote MCP server to claude.ai

In [claude.ai](https://claude.ai/new):

1. Click the **Settings** gear (bottom left).
2. Go to **Connected apps** → **MCP servers** (or **Remote MCP servers**).
3. Click **Add server** (or **+ Add**).
4. Enter:
   - **URL:** `https://synapse.yourdomain.com/mcp/server` (or `http://localhost:8000/mcp/server` for local testing)
   - **Authentication:** Select **Bearer token**
   - **Token:** Paste `<your-MCP_AUTH_TOKEN>` (without the "Bearer " prefix — claude.ai adds it)
5. Click **Connect**. Claude should report "Connected" and list the available tools.

### 5.6b Cloudflare Access in front of the whole app — service tokens (v1.3.9+)

As of **v1.3.9** the recommended production posture puts **Cloudflare Access (Zero
Trust)** in front of the *entire* app, not just optionally on `/mcp/server`. This closes
the "public API in the clear" exposure: every request is gated at the Cloudflare edge
*before* it reaches Synapse.

- **Browser / PWA:** interactive login (One-time PIN or an IdP). The `CF_Authorization`
  cookie then carries same-origin requests through. Nothing to configure in-app.
- **Non-browser clients** (native iOS app, Chrome clipper, `curl`/scripts): they carry no
  CF cookie, so they must send a Cloudflare Access **service token** — two headers on
  every request:

  ```
  CF-Access-Client-Id:     <client-id>.access
  CF-Access-Client-Secret: <client-secret>
  ```

  Create the token in **Zero Trust → Access → Service Auth → Service Tokens**, then add a
  policy to the Synapse Access application with **Action = "Service Auth"** and
  Include → Service Token. Recommendation: **one token per client** so any single one can
  be revoked without touching the others. The iOS app (Settings) and the Chrome clipper
  (Options) each expose fields to paste their Client ID + Secret (v1.3.9); the web
  frontend exposes them under **Settings › Sicurezza / Security**.

#### Remote MCP behind Cloudflare Access

The remote MCP surface (`/mcp/server`) already has its **own** independent auth
(`MCP_AUTH_TOKEN`, Bearer), so you have two options once CF Access gates the app:

- **Claude Desktop (JSON `mcpServers` config)** — this transport lets you set custom
  headers, so add the service token there alongside the Bearer token:

  ```jsonc
  {
    "mcpServers": {
      "synapse_remote": {
        "type": "http",
        "url": "https://synapse.yourdomain.com/mcp/server",
        "headers": {
          "Authorization": "Bearer <MCP_AUTH_TOKEN>",
          "CF-Access-Client-Id": "<client-id>.access",
          "CF-Access-Client-Secret": "<client-secret>"
        }
      }
    }
  }
  ```

- **claude.ai remote-MCP connector** — its UI only supports a **Bearer token**, so it
  cannot send the CF-Access headers. Since `/mcp/server` is already protected by its own
  `MCP_AUTH_TOKEN`, the clean answer is to **exclude that path from the CF Access
  application**: add an Access policy with **Action = "Bypass"** (or a separate,
  unenforced Access app) scoped to `/mcp/server`. The MCP bearer token remains the gate
  for that path — no downgrade in protection, and the connector keeps working. The same
  applies to the clipper ingress `POST /clip` if you prefer a bypass over the in-app
  service-token headers.

### 5.7 Enable remote writes (optional, not recommended)

By default, only the three read tools are exposed. To allow `write_page` (so claude.ai
can create/edit pages in your vault), set:

```bash
export MCP_REMOTE_WRITE_ENABLED=true
docker compose up -d
```

**WARNING:** This allows any bearer-token holder to mutate your vault over the public
internet. Keep `MCP_AUTH_TOKEN` secret and rotate it regularly. For defense-in-depth,
consider using **Cloudflare Access** to add an extra authentication layer at the edge
(the backend requires the token regardless).

### 5.8 Debugging: check the MCP /info endpoint

The REST API exposes `GET /mcp/info` (not the MCP protocol, just a REST endpoint) to
introspect the MCP server's configuration:

```bash
curl -s http://localhost:8000/mcp/info | jq .
```

Expected response (example):

```json
{
  "name": "synapse",
  "http_enabled": true,
  "remote_write_enabled": false,
  "version": "0.5"
}
```

If `http_enabled` is `false`, the token was not set at startup (fail-closed).

The response now also includes `token_source` (`db` | `env` | `none`) and
`allow_without_token` (ADR-0033).

### 5.9 UI token management and allow-without-token (ADR-0033)

The access token can be **generated, rotated, or cleared** from **Settings → API + MCP**
without restarting the stack. The UI calls `PUT /mcp/auth`.

**Token lifecycle:**

- **Generate/rotate:** the UI sends `{ "rotate_token": true }`. A new high-entropy token
  (`secrets.token_urlsafe(32)`) is generated, its PBKDF2-HMAC-SHA256 salted hash is stored
  in `vault_state.mcp_access_token_hash`, and the **plaintext is shown exactly once** in
  the response (`generated_token`). Copy it immediately — it is never retrievable again.
- **Set an explicit token:** send `{ "token": "<value>" }`. The plaintext is hashed and
  stored; `generated_token` is null in the response (you already know the value).
- **Clear:** send `{ "clear_token": true }`. The hash is erased; the surface falls back to
  `MCP_AUTH_TOKEN` env if set, otherwise `token_source` becomes `none`.

**`MCP_AUTH_TOKEN` env as bootstrap fallback:** suitable for headless Docker setups where
you set secrets via env at deploy time. When both the DB hash and the env var are set, the
DB hash takes precedence (`token_source: "db"`). When only the env var is set:
`token_source: "env"`. When neither: `token_source: "none"` and the surface is unauthenticated
(enabled only if `allow_without_token=true` AND the source is private).

**Allow-without-token** (`mcp_allow_without_token`): when set to `true` (via
`{ "allow_without_token": true }` in `PUT /mcp/auth`), requests from **private network
sources** (loopback, CGNAT 100.64.0.0/10, RFC1918, link-local, IPv6 ULA) may reach
`/mcp/server` **without a bearer token**. This is safe for local-only or Tailscale-only
access where the network perimeter is the gate.

**Public (Cloudflare tunnel) sources ALWAYS require a token, regardless of this flag.**
The backend detects Cloudflare headers (`CF-Connecting-IP`, `CF-Ray`) and fails closed
for any source classified as public — `allow_without_token` is never honoured for tunnel
traffic. This is fail-safe by construction: `allow_without_token` can never open the public
surface.

**`MCP_TRUSTED_PROXIES`** (default empty): a comma-separated list of trusted reverse-proxy
IPs whose `X-Forwarded-For` header is used to determine the real client IP. Leave empty
unless you run a local reverse proxy in front of the Synapse backend. The Cloudflare header
check is independent of this setting.

---

## 6. Scheduled folder import (Feature S)

Synapse can periodically scan a folder and import any new or changed documents
automatically. This is configured via the **Automatic import** card in the Settings
section of the UI (or directly via `PUT /import-schedule`).

### 6.1 The mounted-path constraint

The backend container can ONLY see paths that have been explicitly mounted into it via
Docker volumes. There is no host filesystem browse from inside a container. Therefore:

- `source_dir` in the import schedule MUST be a **container-visible** absolute path
  (e.g. `/import`).
- To make a host folder importable, you mount it into the container and enter the
  **container** path in the UI.

This distinction is intentional and is enforced by the backend (a `dir_readable` check
before each scan). If you enter a path that is not visible inside the container, the
scheduler records `last_status="dir_missing"` and the Settings card shows a warning.

### 6.2 Adding the import volume mount

Edit `docker-compose.yml` and uncomment (or add) the example mount in the
`synapse-backend` volumes block:

```yaml
  synapse-backend:
    volumes:
      - ./vault:/vault
      # ── Feature S (ADR-0020): scheduled folder import ────────────────────────
      # Mount any host folder you want Synapse to auto-import into the container,
      # then set the schedule's source_dir to the CONTAINER path (e.g. /import).
      # The backend can ONLY see mounted paths — there is no host filesystem browse.
      - ./import:/import:ro   # read-only is recommended; Synapse copies OUT of it
    environment:
      MAX_UPLOAD_BYTES: "26214400"      # 25 MB upload cap (Feature U, I7)
      IMPORT_SCAN_MAX_FILES: "200"      # per-scan file cap (Feature S, I7)
      IMPORT_SCAN_MAX_SECONDS: "60"     # per-scan wall-clock cap (Feature S, I7)
```

The `:ro` (read-only) flag is recommended: Synapse only reads the source folder and
copies files out of it into `vault/raw/sources/`. It never writes back to `source_dir`.

Create the host folder before the first run:

```bash
mkdir -p /mnt/pool/synapse/import
```

Restart the stack after changing `docker-compose.yml`:

```bash
docker compose down && docker compose up -d
```

### 6.3 Configure the schedule

Once the mount is in place, open **Settings → Automatic import** in the UI:

1. Enable the toggle.
2. Enter the container path: `/import`.
3. Choose a frequency (15 min / 1 h / 6 h / daily).
4. Click **Run now** to trigger an immediate test scan.

The card shows "Last scan: N minutes ago — M imported" after each successful scan.
The `ingest_runs` ledger in the Ingest section shows the per-file ingest outcomes.

### 6.4 Scan limits

Each scheduled scan is bounded by two independent caps (both env-configurable, I7):

| Cap | Default | Env var | Notes |
|-----|---------|---------|-------|
| File cap | 200 files/tick | `IMPORT_SCAN_MAX_FILES` | Remaining files wait for next tick |
| Wall-clock cap | 60 seconds/tick | `IMPORT_SCAN_MAX_SECONDS` | Scan stops early; partial count reported |

By default the scan is **non-recursive**: only files directly inside `source_dir` are
imported; subdirectories are skipped. Set `IMPORT_SCAN_RECURSIVE=true` to enable
recursive traversal. When enabled, the subdirectory path segments are passed to the
analysis prompt as a `folderContext` hint (e.g. `reports/2026/q2`), which helps the AI
classify content correctly when folder names carry semantic meaning.

As of v0.8 (M7), `.md`, `.txt`, `.markdown`, `.pdf`, `.docx`, `.pptx`, `.xlsx`,
`.png`, `.jpg`, `.jpeg`, `.webp`, `.mp3`, `.m4a`, `.wav`, `.mp4`, `.mov`, and `.webm`
files are imported (F12, ADR-0025). Binary office files are converted to companion
`.extracted.md` files automatically before ingest. Image captioning requires
`VISION_CAPTIONS_ENABLED=true` and audio/video transcription requires
`AV_TRANSCRIPTION_ENABLED=true` (see §2.1). Other file types are silently skipped.

---

## 7. Desktop app (macOS / Windows) {#desktop-app}

This section covers the Tauri v2 desktop binaries (F15, ADR-0047). The desktop app is
optional — the PWA served from the backend is the primary distribution and requires no
build step. The desktop app is the right choice when you want a native window on a single
machine and prefer not to keep a browser tab open.

> **Scope:** macOS and Windows only. Linux targets from ADR-0039 remain defined but are
> not shipped in v0.6 and are not covered here.

### 7.1 Install from a GitHub release (recommended)

Unsigned pre-built binaries are attached to every release tagged `desktop-v*`:

```
https://github.com/<owner>/synapse/releases
```

Download the installer for your OS:

| OS | Artifact | Install |
|----|----------|---------|
| **macOS (Apple Silicon)** | `Synapse_<ver>_aarch64.dmg` | Open the DMG, drag to Applications |
| **macOS (Intel)** | `Synapse_<ver>_x64.dmg` | Open the DMG, drag to Applications |
| **Windows** | `Synapse_<ver>_x64_en-US.nsis.exe` | Run the installer |

#### Unsigned-binary warnings (expected — read before installing)

The v0.6 binaries are **not code-signed or notarized**. First launch on both operating
systems will show a security warning. This is expected. Follow the steps for your OS:

**macOS (Gatekeeper):**

1. In Finder, right-click the app icon and choose **Open** (do not double-click).
2. A dialog appears warning that the developer is not verified. Click **Open** again.
3. If the dialog does not offer an Open button, go to **System Settings → Privacy &
   Security** and look for the "Synapse was blocked" message, then click **Open Anyway**.

> macOS 15 (Sequoia) moves the Privacy & Security prompt. If "Open Anyway" does not
> appear in the expected location, use the right-click → Open path described above.

**Windows (SmartScreen):**

1. Run the installer. Windows Defender SmartScreen will say "Windows protected your PC".
2. Click **More info**, then **Run anyway**.

### 7.2 First-launch Connect screen

The desktop app does not know the address of your Synapse backend at install time.
On the very first launch (and any time the stored URL is cleared), you will see a
full-screen branded **Connect** screen.

**What to enter:**

- The base URL of your running Synapse backend, e.g.:
  - `http://localhost:8000` — backend on the same machine
  - `http://truenas:8000` — backend on your TrueNAS via Tailscale MagicDNS
  - `https://synapse.yourdomain.com` — backend behind a Cloudflare Tunnel (HTTPS)
- No trailing slash. Scheme (`http://` or `https://`) is required.

**What happens on Connect:**

1. The app sends a `GET /status` probe to the URL you entered.
2. If the probe returns 2xx within the timeout, the URL is saved to the app's local
   storage (`synapse.serverUrl`) and the full Synapse interface loads.
3. If the probe fails or times out, a `connect.error.*` message appears and the Connect
   screen stays open — the URL is **not** saved. Check that the backend is running and
   reachable from your machine, then try again.

**The URL is never saved until the probe succeeds**, so a wrong address cannot brick the
app. To start over, use the **Change server** button in the header (see §7.3).

The Connect screen only appears in the Tauri desktop app. The PWA and browser-based
access are unaffected — they continue to use a relative URL and never show this screen.

### 7.3 Header server chip and Change server

Once connected, the header bar shows a small chip with the connected backend hostname.
Clicking the chip (or choosing **Change server** from the chip's dropdown) clears the
stored URL and returns to the Connect screen. Use this to:

- Switch to a different backend (e.g. from `localhost` while developing to your TrueNAS
  backend when deploying).
- Re-enter the URL after changing your network setup.

The change-server action is only visible in the Tauri desktop app.

### 7.4 CORS — required backend configuration

The Tauri webview issues cross-origin requests to your backend (`tauri://localhost` →
`http://…:8000`), so the backend's CORS allow-list must include the webview origins.

The default `CORS_ALLOW_ORIGINS` already includes them:

```
tauri://localhost          (macOS / Linux WebKit)
http://tauri.localhost     (Windows WebView2)
```

**If you override `CORS_ALLOW_ORIGINS`** in your `.env`, you MUST keep both webview
origins in the list. Example for TrueNAS with a Cloudflare Tunnel:

```env
CORS_ALLOW_ORIGINS=tauri://localhost,http://tauri.localhost,https://synapse.yourdomain.com
```

> **Critical:** the backend middleware runs with `allow_credentials=True`. The CORS
> spec forbids the `*` wildcard when credentials are involved — origins must be listed
> explicitly. A `*` wildcard will silently break all authenticated requests from the
> desktop app. Never use `*` here.

### 7.5 macOS mixed-content note

On macOS, the Tauri webview origin is `tauri://` (a secure context). If your backend
runs on plain `http://` (not HTTPS), WebKit's mixed-content policy **may block the
connection** — the same restriction that prevents `https://` sites from loading `http://`
resources.

> **Verified 2026-07-03:** the release build of Synapse.app on macOS 26 successfully
> reached an `http://localhost:8000` backend (probe logged server-side, 200 OK) — WebKit
> did NOT block the request. The fallback below remains documented in case a future
> macOS/WebKit version tightens the policy.

**If requests are blocked on macOS against an `http://` backend:**

- Prefer HTTPS: expose the backend via a Cloudflare Tunnel or Tailscale with TLS. HTTPS
  backends (`https://`) are not affected by mixed-content restrictions.
- Fallback (development only): the documented code path is `@tauri-apps/plugin-http`,
  which routes requests through the Rust layer, bypassing WebKit's mixed-content gate.
  This is not wired in v0.6 — it is the documented next step if the live macOS build
  blocks `http://` backends (ADR-0047 §2.4, risk 1).

### 7.6 Build from source

If you prefer to build the desktop app yourself (required for code-signing with your own
certificate or for local development iteration):

**Prerequisites:**

```bash
# Install Rust toolchain via rustup (once per machine)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# Verify
rustc --version
cargo --version
```

macOS and Windows ship a native WebKit/WebView2 runtime; no additional WebView
development libraries are needed.

**Build:**

```bash
# From the repo root
source "$HOME/.cargo/env"          # ensure Rust is on PATH (add to .bashrc/.zshrc to make permanent)
npm ci --prefix frontend           # install JS dependencies
npm --prefix frontend run tauri:build
```

Build artifacts land in `src-tauri/target/release/bundle/`:

| OS | Path | Contents |
|----|------|----------|
| macOS | `bundle/dmg/` | `.dmg` installer |
| macOS | `bundle/macos/` | `.app` bundle (for direct drag-to-Applications) |
| Windows | `bundle/nsis/` | `.nsis.exe` NSIS installer |

> The build produces the same unsigned binaries as the CI release. If you need signed
> artifacts for distribution (code-signing / notarization), see
> `docs/adr/0047-desktop-runtime-server-url-and-connect-gate.md` §4 (risk 2) for
> what is deferred and why.

### 7.7 Release procedure (v* unified tag, ADR-0049)

Starting with v0.7.0 the `desktop-v*` tag channel is **replaced** by a unified `v*`
tag channel (e.g. `v0.7.0`). One tag produces both the backend release notes and the
signed desktop installer artifacts with auto-update support (ADR-0049). The old
`desktop-v*` trigger no longer exists.

#### Step 1 — Three-way version bump

Before tagging, bump the version in **all three files together** (a mismatch breaks the
updater's version compare and will silently prevent installed clients from updating):

| File | Field | Example |
|------|-------|---------|
| `src-tauri/tauri.conf.json` | `"version"` | `"0.7.0"` |
| `src-tauri/Cargo.toml` | `version = "..."` under `[package]` | `"0.7.0"` |
| `frontend/package.json` | `"version"` | `"0.7.0"` |

Commit the three-file bump with the message:
```
chore: bump version to v0.7.0 [F15]
```

#### Step 2 — Tag and push

```bash
git tag v0.7.0
git push origin v0.7.0
```

The `.github/workflows/desktop-release.yml` CI workflow triggers on `refs/tags/v*`.

#### Step 3 — CI matrix

| Runner | Target | Artifacts |
|--------|--------|-----------|
| `macos-latest` | `.app.tar.gz` + `.sig`, `.dmg` | Attached to the GitHub release |
| `windows-latest` | NSIS `.exe` + `.sig` | Attached to the GitHub release |
| Both runners | `latest.json` (merged, both platforms) | Attached to the GitHub release |

The workflow passes `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
(GitHub Actions secrets) to `tauri-action` with `includeUpdaterJson: true`. Each
artifact is minisign-signed. `tauri-action` merges both platform entries into a single
`latest.json` on the release.

#### Step 4 — Acceptance gate

Before announcing the release, verify:

1. The GitHub release assets contain both `darwin-aarch64` and `windows-x86_64` entries
   in `latest.json`.
2. The `version` field in `latest.json` matches the tag and all three bumped files.
3. The `.sig` files are present for each platform bundle (unsigned OS binary, signed by
   our minisign key).

If any check fails, re-run the workflow (`workflow_dispatch`) or fix and re-tag.

#### Key-loss caveat (CRITICAL)

The minisign private key (`TAURI_SIGNING_PRIVATE_KEY` + password; local copy at
`~/.tauri/`) is the **sole trust root** for the auto-update chain. Every installed
desktop client verifies incoming updates against the public key embedded in the binary.

**If the private key is lost, all installed clients can no longer receive auto-updates.**
A new keypair would produce signatures that all existing clients reject; the only
recovery for end-users is a manual re-install of a new binary carrying the new public
key. Keep a secure off-machine backup of the private key and its password, and do not
rotate the GitHub secret without a coordinated re-release.

#### `workflow_dispatch` (build-only)

Triggering the workflow manually (`workflow_dispatch`, no tag) produces the installers
but does **not** create or update a GitHub release and does **not** produce `latest.json`.
Use this for build verification only.

---

## 8. TrueNAS SCALE deployment

### 8.1 Deploy via SSH

```bash
ssh admin@truenas.local

git clone https://github.com/<owner>/synapse.git /mnt/pool/synapse/synapse-repo
cd /mnt/pool/synapse/synapse-repo

# Create .env from the example and edit it
cp .env.example .env
nano .env   # or vi .env

docker compose up -d
docker compose logs -f synapse-backend
```

### 8.2 Vault bind mount

The `vault/` directory in the repo root is bind-mounted at `/vault` inside the
`synapse-backend` container (see `docker-compose.yml`). On TrueNAS you may want to
bind from the ZFS dataset instead:

Edit `docker-compose.yml` volumes section:

```yaml
volumes:
  - /mnt/pool/synapse/vault:/vault
```

### 8.3 Networking

**Tailscale (internal):** all TrueNAS services are on the same Tailscale mesh. Access
the backend API at `http://truenas-node-ip:8000` or `http://truenas.local:8000`.

**Cloudflare Tunnel (public HTTPS, optional):**

1. Configure a Cloudflare Tunnel on TrueNAS (one-time, at host level).
2. Add an ingress rule: `synapse.yourdomain.com` → `http://localhost:8000`.
3. Expose the frontend origin in `CORS_ALLOW_ORIGINS`.

---

## 9. Updating Synapse {#updating-synapse}

### 9.1 Manual updates

The simplest way to update Synapse on TrueNAS is via the command line:

```bash
docker compose pull              # Download the latest backend image
docker compose up -d             # Restart services with the new image
```

The backend service will restart with the new image. The Alembic migration runner
(part of the startup sequence) automatically applies any pending database schema
migrations — you do not need to run migrations manually.

**Postgres and Qdrant:** the `pull` command only updates the `synapse-backend` service
when using the GHCR image (see §9.2). Data services are not updated by default,
which is correct and intentional — database containers should never be auto-updated
without reading the release notes first.

### 9.2 Automatic server updates (optional)

Synapse supports zero-touch backend updates via **Watchtower**, an open-source container
watcher. Watchtower runs as an optional Docker Compose service (disabled by default via
a `autoupdate` profile) and polls the container registry hourly for new backend images.
When an update is available, Watchtower automatically pulls the new image and restarts
the backend service. The restart is graceful: existing connections are allowed to drain,
and the new container starts immediately after.

#### 9.2a Setting up Watchtower

**Prerequisites:**
- You must switch `docker-compose.yml` to use the GHCR image instead of building from source.
- The backend service has the label `com.centurylinklabs.watchtower.enable=true` (already set).

**Edit `docker-compose.yml`:**

Comment out the `build:` block and uncomment the `image:` line in the `synapse-backend` service:

```yaml
synapse-backend:
  # image: ghcr.io/emanuele-chiummo/llm-wiki-synapse-backend:latest
  build:
    context: ./backend
    dockerfile: Dockerfile
```

becomes:

```yaml
synapse-backend:
  image: ghcr.io/emanuele-chiummo/llm-wiki-synapse-backend:latest
  # build:
  #   context: ./backend
  #   dockerfile: Dockerfile
```

**Enable the Watchtower service:**

```bash
docker compose --profile autoupdate up -d
```

This starts Watchtower alongside the backend, postgres, and qdrant services. Watchtower
runs in the background and checks the GHCR registry every hour (configurable via the
`--interval` flag in the service definition).

**Verify Watchtower is running:**

```bash
docker compose logs -f watchtower | head -20
# You should see logs like:
# watchtower_1  | time="2026-07-03T12:00:00Z" level=info msg="Watchtower 1.x.x started"
# watchtower_1  | time="2026-07-03T12:05:00Z" level=info msg="Checking synapse-backend..."
```

#### 9.2b On-demand updates from the app (the "Update system" button)

Settings → **Info** shows the running version and — when a newer GitHub Release exists — an
**"Update system"** button. Clicking it triggers Watchtower to pull the latest images and recreate
the labelled containers immediately, so you never run `docker pull` by hand.

This needs Watchtower's HTTP API, gated by a **shared token** both services read from
`WATCHTOWER_HTTP_API_TOKEN`:

```bash
# .env (next to docker-compose.yml)
WATCHTOWER_HTTP_API_TOKEN=$(openssl rand -hex 32)
```

The compose already wires it: the `watchtower` service starts with `--http-api-update`
(plus `--http-api-periodic-polls`, so the hourly poll stays active too), and the backend receives
`WATCHTOWER_URL=http://watchtower:8080` + the same token so `POST /ops/system-update` can reach it.
When the token is empty the app reports `update_supported=false` and **hides the button** — the
availability line still shows so you can update manually.

Notes:
- **No download percentage.** Watchtower's API is fire-and-forget, so the UI shows an indeterminate
  "in progress" state, not a byte-level percentage — a deliberate trade-off (B1) to keep Docker
  privileges out of the backend. The backend is recreated too, so the app briefly disconnects and
  reconnects on the new version.
- **Which containers update:** every container on the host labelled
  `com.centurylinklabs.watchtower.enable=true`, regardless of which compose defined it. If your
  **frontend** runs as a separate container/app (production serves the UI separately), add the same
  label there for the button to refresh it too.
- Availability is checked against the public GitHub Releases API (`UPDATE_CHECK_REPO`, cached ~1h);
  the check is read-only and never blocks the UI.

#### 9.2c Which services are auto-updated

The backend and marker services carry the Watchtower enable label:

```yaml
labels:
  com.centurylinklabs.watchtower.enable: "true"
```

Postgres and Qdrant **do NOT** carry this label. This is intentional and safe:

- **Backend updates are safe:** Alembic migrations (run at startup) ensure the backend
  can work with older or newer database schemas. Releases are backward-compatible (no
  hard breaks). A restart takes ~10 seconds.
- **Database updates are NOT safe without review:** Schema changes, index rewrites, and
  data migrations require coordination. Always read the release notes for Postgres and
  Qdrant before updating. **Never allow a database container to auto-update blindly.**

#### 9.2c Safety: version mismatch banner

The frontend also receives backend version information via `GET /status` (AC-R12-3-3).
If the backend and frontend versions differ (and the backend version is not `"dev"`),
the app displays a non-blocking, dismissible banner:

```
A server update is available (backend v1.2.0 / frontend v1.1.5).
Pull the new image on TrueNAS to update.
```

This banner is:
- **Non-blocking:** all functionality works; the mismatch is informational only.
- **Dismissible:** click the dismiss button or the banner disappears for the session.
- **Not shown when matching:** if backend and frontend versions are the same, no banner.

The banner serves as a safety net: if Watchtower auto-updates the backend but the
frontend is not reloaded in the browser, the user is reminded to refresh.

#### 9.2d Disabling Watchtower

To turn off auto-updates and go back to manual updates:

```bash
docker compose --profile autoupdate down watchtower
# or simply:
docker compose down
docker compose up -d  # (without --profile autoupdate)
```

Existing deployments that do NOT use the `--profile autoupdate` flag will never start
Watchtower — it is off by default.

### 9.3 TrueNAS SCALE Custom App update button

If you deployed Synapse via the TrueNAS Custom App interface (instead of CLI Docker
Compose), TrueNAS may show an **Update available** button in the App card. Clicking it
initiates an update of the backend image (if the Custom App is configured to pull from GHCR).

**This is a semi-automatic option:** TrueNAS handles the pull and restart, but you control
the timing — there is no polling interval like Watchtower. Check the TrueNAS app update
UI for status.

### 9.4 Diun — notify-only (most conservative)

**Diun** (https://github.com/crazy-max/diun) is a container image notification daemon.
Unlike Watchtower, Diun **does not update containers**; it only watches the registry and
sends you a notification (email, Slack, webhook) when a new backend image is available.
You then manually pull and restart at your convenience.

**Use Diun if:**
- You want to review release notes before updating.
- You prefer explicit control over restart timing (e.g. no auto-restart during business hours).
- You run a production environment and want human approval before any image update.

Setup:

```bash
# Create a Diun config and run it as a separate Docker container.
# See https://diun.io/docs/usage/overview for full instructions.
```

### 9.5 Database updates — manual + reviewed

**IMPORTANT:** Postgres and Qdrant containers should **never** be auto-updated via Watchtower
or any automatic mechanism. Always:

1. **Read the release notes** for the new version.
2. **Test in a staging environment** (or with a backup).
3. **Plan a maintenance window** if needed (e.g. long migrations).
4. **Update manually** via `docker compose pull postgres` and `docker compose up -d`.

Example Postgres backup before updating:

```bash
# Dump the current database
docker compose exec -T postgres pg_dump \
  -U synapse -d synapse \
  | gzip > /backup/synapse-pre-upgrade-$(date +%Y%m%d-%H%M%S).sql.gz

# Check the new version
docker pull postgres:16-alpine
# (review the release notes)

# Update and restart
docker compose pull postgres
docker compose up -d postgres
```

---

## 11. Useful make targets

```bash
make er         # Regenerate docs/er/schema.mmd from SQLAlchemy models
make openapi    # Regenerate docs/api/openapi.json from FastAPI app
make test       # Run pytest unit tests (no external services needed)
make lint       # ruff + black check
make typecheck  # mypy strict
make screenshots  # Playwright E2E screenshot capture (requires running stack)
```

Run `make er` and `make openapi` after any schema migration and commit the results.
The docs gate CI job will fail on drift.

---

## 12. Backup strategy

### 12.0 Built-in scheduled backup (1.9.1 W4, SEC-OPS-2)

Synapse ships its own scheduled `pg_dump`, so §12.1's manual cron/periodic-task recipe is
now OPTIONAL — enable the built-in job instead and skip straight to §12.2 for the vault
filesystem (ZFS snapshots remain the right tool for that part).

The backup op runs through the SAME `OpsScheduler` used by lint / domain-backfill /
schema-review / type-reclassify (`backend/app/ops_scheduler.py`) — one more `off|hourly|
daily|weekly` schedule key, no new infrastructure:

```bash
# Enable a daily backup (any authenticated client — curl, the Settings UI, etc.)
curl -X PUT http://localhost:8000/config/app/backup_schedule \
     -H "Content-Type: application/json" \
     -d '{"value": "daily"}'

# Trigger one immediately (does not wait for the schedule)
curl -X POST http://localhost:8000/ops/schedules/backup/run-now

# Check status (last_run_at, last_status, last_detail, in_flight)
curl http://localhost:8000/ops/schedules
```

Where archives land (env vars — see `.env.example`):

| Variable | Default | Notes |
|----------|---------|-------|
| `BACKUP_DIR` | `<VAULT_PATH>/.synapse/backups` | Absolute path override; put it on the SAME backup-friendly volume as `vault/` (e.g. a TrueNAS dataset with its own snapshot schedule) so one ZFS snapshot policy covers both. |
| `BACKUP_RETENTION_COUNT` | `7` | Archives beyond the newest N (per vault) are deleted after each successful run — bounded disk growth (I7). |
| `BACKUP_TIMEOUT_SECONDS` | `300` | Hard wall-clock cap on the `pg_dump` subprocess; a stuck dump is killed, not left hanging. |
| `PG_DUMP_PATH` | `pg_dump` | Override only for non-standard installs; the shipped image already has `postgresql-client` installed. |

Archives are `pg_dump -Fc` (custom/compressed format) — restore with `pg_restore`, same as
§20.3 below. **A pre-update dump also runs automatically**: `POST /ops/system-update` (the
Home "Update system" button) runs one backup FIRST, before poking Watchtower, so an update
that breaks the schema/app has a fresh rollback point. That pre-update dump is best-effort
— a dump failure is logged (visible in `GET /ops/schedules`) but never blocks the update
itself, since refusing an explicitly-requested update because the safety-net dump failed
would be a worse outcome.

### 12.1 Postgres (manual — still works, now optional)

```bash
# Dump from inside the container
docker compose exec -T postgres pg_dump \
  -U synapse -d synapse \
  | gzip > /mnt/pool/synapse/postgres-backups/synapse-db-$(date +%Y%m%d-%H%M%S).sql.gz
```

Automate with a TrueNAS periodic task or cron job — or use §12.0's built-in scheduler
instead, which needs no host-side cron entry.

### 12.2 Vault filesystem

The `vault/` directory contains the raw documents and AI-generated wiki pages. Back
it up with a ZFS snapshot:

```bash
zfs snapshot pool/synapse@backup-$(date +%Y%m%d)
```

---

## 13. CI/CD

### 13.1 CI stages

| Stage | Trigger | Required | Purpose |
|-------|---------|----------|---------|
| `lint` | push / PR | Yes | ruff + black checks |
| `typecheck` | push / PR | Yes | mypy strict mode |
| `unit` | push / PR | Yes | pytest (no external services) |
| `docs` | push / PR | Yes | ER + OpenAPI drift check; mmdc Mermaid render |
| `integration` | manual | Optional | docker-compose E2E (requires live TrueNAS services) |

### 13.2 Docs gate

The `docs` stage runs `make er` and `make openapi`, then diffs the output against the
committed files. A mismatch fails the PR. Fix it with:

```bash
make er
make openapi
git add docs/er/schema.mmd docs/api/openapi.json
git commit -m "docs: refresh ER and OpenAPI [I8]"
```

---

## 14. Troubleshooting

### 14.1 "connection refused" on EMBEDDING_URL or QDRANT_URL

Cause: the external service is not running or the Docker container cannot reach the
host network.

```bash
# Verify the external service is up
curl -s http://100.x.x.x:11434/api/tags | jq .
curl -s http://100.x.x.x:6333/health

# Verify docker can reach the host
docker run --rm alpine ping -c 1 host.docker.internal
```

### 14.2 EMBEDDING_DIM mismatch

Cause: the `EMBEDDING_DIM` env var does not match the actual output of the embedding
model.

For the default Ollama / bge-m3 setup (`EMBEDDING_FORMAT=ollama`):

```bash
curl -s -X POST http://100.x.x.x:11434/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "bge-m3", "prompt": "test"}' | jq '.embedding | length'
# Returns the actual dimension — update EMBEDDING_DIM to match
docker compose restart synapse-backend
```

If you are using an OpenAI-compatible endpoint (`EMBEDDING_FORMAT=openai`), verify that
`EMBEDDING_FORMAT` is set correctly and that `EMBEDDING_API_KEY` is provided if the
endpoint requires authentication. A malformed response (wrong format setting) surfaces
as an `EmbeddingError` in the backend logs. See §2.1 env var reference for the
`EMBEDDING_FORMAT` and `EMBEDDING_API_KEY` variables. (ADR-0031)

### 14.3 Pre-existing files are not ingested on startup

By design (incremental index — the watcher picks up new and modified files only).
To ingest files that existed before Synapse started, trigger a run manually:

```bash
curl -X POST http://localhost:8000/ingest/trigger
```

Or use the **Run Ingest** button in the Ingest section of the web UI.

### 14.4 No provider_config row — hard error on ingest or chat

If the application logs "no provider_config found for scope=global", insert at least
one global row (see §4). A missing global row is never silently ignored.

### 14.5 Chat provider returns an error with CLI backend

`CliAgentProvider.chat()` is implemented in v0.5 as delegated streaming chat (bounded
by `CHAT_AGENT_MAX_TURNS`, `token_budget`, and `timeout_seconds`). If it errors, check
that `ANTHROPIC_API_KEY` is set (the CLI backend requires it), and that the model ID in
`provider_config` is a valid Claude model name. Ingest with CLI works independently.

### 14.6 Scheduled import: last_status="dir_missing"

The import schedule is enabled and a `source_dir` is configured, but scans report
`dir_missing`.

Cause: the path is not visible inside the container. The backend can only see mounted
paths.

Fix:
1. Verify the volume mount is in `docker-compose.yml` (see §6.2).
2. Restart the stack: `docker compose down && docker compose up -d`.
3. Verify the path exists inside the container:

```bash
docker compose exec synapse-backend ls /import
```

4. In Settings → Automatic import, confirm the `source_dir` field shows the container
   path (e.g. `/import`), not a host path.

### 14.7 Uploaded file is rejected with 415

Cause: the file extension is not in the accepted list.

As of v0.8 (M7), accepted formats are: `.md`, `.txt`, `.markdown`, `.pdf`, `.docx`,
`.pptx`, `.xlsx`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.mp3`, `.m4a`, `.wav`, `.mp4`,
`.mov`, `.webm` (F12, ADR-0025). The 415 response body names the extension and the
full acceptance list explicitly. Files with unrecognized extensions are rejected.

---

## 15. Security — Deployment mode and shared Bearer token (ADR-0052 / ADR-0075) {#security}

### 15.1 Overview

The REST API uses one env-only shared Bearer token (ADR-0052). Release 1.6.0 adds an explicit
deployment trust boundary (ADR-0075):

- `SYNAPSE_DEPLOYMENT_MODE=local` is the backward-compatible default. An empty token is accepted
  for loopback development and trusted single-machine use.
- `SYNAPSE_DEPLOYMENT_MODE=server` is required for every network-reachable backend. Startup fails
  closed if the token is missing or obviously weak.

When configured, the single middleware gate performs one constant-time
`secrets.compare_digest` per request (I3). A Cloudflare Tunnel, Cloudflare Access, Tailscale or
another reverse proxy is defence in depth; it does not replace the application token in `server`
mode.

### 15.2 Enabling the token

1. Generate a cryptographically secure token. Do not use a password, placeholder or repeated
   character sequence:

   ```bash
   openssl rand -hex 32
   # Example shape: 64 random hexadecimal characters
   ```

2. Add the mode and token to `.env` before the same restart (never commit `.env`):

   ```env
   SYNAPSE_DEPLOYMENT_MODE=server
   SYNAPSE_AUTH_TOKEN=<paste-the-generated-token>
   ```

   Server validation requires at least 32 characters, no whitespace and at least eight distinct
   characters. It rejects weak configuration but does not replace secure random generation.

3. Restart the stack:

   ```bash
   docker compose down && docker compose up -d
   ```

4. Verify the gate is active by calling a protected route without a token:

   ```bash
   curl -s -w "\nStatus: %{http_code}\n" http://localhost:8000/pages | tail -1
   # Expected: Status: 401
   ```

   And with the token:

   ```bash
   curl -s -H "Authorization: Bearer <your-token>" http://localhost:8000/pages | head -5
   # Expected: 200 with a JSON response
   ```

5. Exempt endpoints (always reachable, even with the token set):

   | Endpoint | Why exempt |
   |----------|-----------|
   | `GET`/`HEAD /status` | Existing desktop connection probe; no page content or credentials |
   | `GET`/`HEAD /health/live` | Minimal liveness response only (`{"status":"ok"}`) |
   | `GET`/`HEAD /docs`, `/openapi.json`, `/redoc` | API schema is public; not vault data |
   | All `OPTIONS` requests | CORS preflight cannot carry an `Authorization` header |
   | `/mcp/server/*` | MCP HTTP surface has its own bearer token (ADR-0033) |
   | `POST /clip` | Web-clipper ingress has its own `CLIP_TOKEN` (ADR-0038) |

   `GET`/`HEAD /health/detailed` is protected because it exposes component and operational
   diagnostics. Call it with `Authorization: Bearer <token>`.

### 15.3 Token rotation

Server-side rotation requires an env change and a container restart — there is no
rotation endpoint. There is deliberately no DB storage of this credential.

1. Generate a new token (`openssl rand -base64 32`).
2. Update `SYNAPSE_AUTH_TOKEN` in `.env` or `docker-compose.yml`.
3. Restart: `docker compose down && docker compose up -d`.
4. In the Synapse client (desktop or web), go to **Settings > Security** and paste the new token.

The client stores the token per-server in `localStorage` (`synapse.authToken`). Entering the
new token in Settings > Security is a client-side update only — it does not call the backend
(§2.1 of ADR-0052).

### 15.4 Client UX when auth is enabled

- **Desktop (Tauri) ConnectScreen:** a password field labelled "Access token" appears below
  the server URL field. Enter the token before clicking Connect. If the token is wrong, the
  Connect screen stays open and shows an inline error.
- **Web/PWA (browser):** if a 401 is received on any request, the app shows a token-entry
  overlay. Entering the correct token re-attempts the request and proceeds automatically.
- **Settings > Security:** update the stored client token at any time, e.g. after a server-side
  rotation. The panel shows the current server URL (read-only) and a "Rotate token" field with
  a show/hide toggle. A note explains the server-side rotation procedure (env + restart).
- **MCP clients and web clipper are unaffected.** They use their own tokens (`MCP_AUTH_TOKEN` /
  `CLIP_TOKEN`) and are never gated by `SYNAPSE_AUTH_TOKEN`.

### 15.5 Migrating from `local` to `server`

No database migration is needed. Treat the change as an atomic client-and-probe rollout:

1. Confirm every REST client can store and send `SYNAPSE_AUTH_TOKEN`.
2. Generate the token with `openssl rand -hex 32` and store it in the deployment secret manager or
   uncommitted `.env` file.
3. Replace unauthenticated `/health/detailed` liveness probes with `/health/live`. For dashboards
   that need component diagnostics, retain `/health/detailed` and configure its bearer header.
4. Set `SYNAPSE_DEPLOYMENT_MODE=server` and `SYNAPSE_AUTH_TOKEN` together, then restart.
5. Verify the boundary:

   ```bash
   curl -i http://localhost:8000/health/live
   # Expected: 200 without a token

   curl -i http://localhost:8000/health/detailed
   # Expected: 401

   curl -i -H "Authorization: Bearer <your-token>" \
     http://localhost:8000/health/detailed
   # Expected: 200

   curl -i http://localhost:8000/pages
   # Expected: 401
   ```

If startup fails, inspect the configuration error and replace the missing or weak token. Reverting
to `local` disables the fail-closed guarantee and is an acceptable rollback only after restricting
the backend to loopback or another equivalently trusted single-machine boundary.

---

## 16. Code signing and notarization (R10-3) {#code-signing}

### 16.1 Why code signing

The Tauri v2 desktop binaries included in GitHub Releases are **unsigned by default**.
Unsigned binaries trigger OS security warnings on every install:

- **macOS (Gatekeeper):** "Synapse.app is damaged or can't be opened" or "unidentified developer".
- **Windows (SmartScreen):** "Windows protected your PC — unrecognized app".

Users can bypass these warnings (right-click → Open on macOS; More info → Run anyway on Windows)
but the warnings impede adoption. Code signing + notarization removes them entirely.

---

### 16.2 macOS — Apple Developer Program + notarization

**Prerequisites:**

- An Apple Developer Program membership (US$99/year). Enroll at
  [developer.apple.com](https://developer.apple.com/programs/enroll/).
- A **Developer ID Application** certificate. Create it in
  Xcode > Settings > Accounts > Manage Certificates (or in the Apple Developer portal >
  Certificates, Identifiers & Profiles). Export the `.p12` file with a password.

**Secrets to configure in GitHub Actions:**

Add the following via `gh secret set`:

```bash
gh secret set APPLE_CERTIFICATE         # base64-encoded .p12 file
gh secret set APPLE_CERTIFICATE_PASSWORD # password for the .p12
gh secret set APPLE_SIGNING_IDENTITY    # e.g. "Developer ID Application: Your Name (TEAMID)"
gh secret set APPLE_ID                  # your Apple ID email
gh secret set APPLE_PASSWORD            # app-specific password (not your Apple ID password)
                                         # generate at appleid.apple.com > Sign-In and Security > App-Specific Passwords
gh secret set APPLE_TEAM_ID            # 10-char team ID (visible in developer.apple.com top-right)
```

To encode the `.p12` as base64:

```bash
base64 -i ~/Certificates/DeveloperID.p12 | tr -d '\n' | pbcopy
# Then: gh secret set APPLE_CERTIFICATE  (paste from clipboard)
```

**`tauri.conf.json` changes:**

In `src-tauri/tauri.conf.json`, set the macOS signing identity and enable notarization:

```json
{
  "bundle": {
    "macOS": {
      "signingIdentity": "$APPLE_SIGNING_IDENTITY",
      "notarizationCredentials": {
        "appleId": "$APPLE_ID",
        "appleIdPassword": "$APPLE_PASSWORD",
        "teamId": "$APPLE_TEAM_ID"
      }
    }
  }
}
```

`tauri-action` reads these from the environment at build time — no hardcoded values.

**Workflow changes (`desktop-release.yml`):**

The `tauri-action` step already accepts all notarization env vars. Add the secret references:

```yaml
- name: Build and release
  uses: tauri-apps/tauri-action@v0
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    APPLE_CERTIFICATE: ${{ secrets.APPLE_CERTIFICATE }}
    APPLE_CERTIFICATE_PASSWORD: ${{ secrets.APPLE_CERTIFICATE_PASSWORD }}
    APPLE_SIGNING_IDENTITY: ${{ secrets.APPLE_SIGNING_IDENTITY }}
    APPLE_ID: ${{ secrets.APPLE_ID }}
    APPLE_PASSWORD: ${{ secrets.APPLE_PASSWORD }}
    APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
    TAURI_SIGNING_PRIVATE_KEY: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}
    TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}
```

**What signing + notarization does:**

1. The macOS runner signs the `.app` bundle with the Developer ID certificate.
2. `tauri-action` submits the signed `.dmg` to Apple's notarization service (using `notarytool`
   under the hood). This step takes 1–5 minutes.
3. Apple staples the notarization ticket to the `.dmg`.
4. Users can now double-click the `.dmg` and drag `Synapse.app` to Applications with no Gatekeeper
   warning on macOS 10.15 Catalina and later.

> **Until the secrets are configured and a signed release is cut**, installs require the
> right-click → Open workaround documented in §7.1.

---

### 16.3 Windows — OV or EV code-signing certificate

**Prerequisites:**

- A **code-signing certificate** from a trusted CA:
  - **OV (Organization Validated):** issued to an organization; removes SmartScreen from
    `.exe`/`.msi` after the certificate builds reputation. Cost: ~US$200–500/year.
  - **EV (Extended Validation):** requires a hardware token (USB) or HSM; SmartScreen trust is
    immediate. Cost: ~US$500+/year. For individual/hobby projects the OV route is usually
    sufficient, though it takes longer to build reputation with Microsoft's SmartScreen ML model.
- Export the certificate as a `.pfx` (PKCS#12) file with a password.

**Secrets to configure in GitHub Actions:**

```bash
gh secret set WINDOWS_CERTIFICATE         # base64-encoded .pfx file
gh secret set WINDOWS_CERTIFICATE_PASSWORD # password for the .pfx
```

To encode the `.pfx`:

```bash
base64 -i ~/Certificates/SynapseSign.pfx | tr -d '\n'
# Copy the output and: gh secret set WINDOWS_CERTIFICATE
```

**`tauri.conf.json` changes:**

```json
{
  "bundle": {
    "windows": {
      "certificateThumbprint": null,
      "digestAlgorithm": "sha256",
      "timestampUrl": "http://timestamp.digicert.com"
    }
  }
}
```

`tauri-action` reads `WINDOWS_CERTIFICATE` and `WINDOWS_CERTIFICATE_PASSWORD` automatically and
calls `signtool.exe` on the Windows runner to sign the NSIS `.exe` installer.

**Workflow changes (`desktop-release.yml`):**

Add the Windows secrets to the `tauri-action` env block:

```yaml
env:
  WINDOWS_CERTIFICATE: ${{ secrets.WINDOWS_CERTIFICATE }}
  WINDOWS_CERTIFICATE_PASSWORD: ${{ secrets.WINDOWS_CERTIFICATE_PASSWORD }}
```

**What signing does:**

1. The Windows runner signs the NSIS `.exe` installer with `signtool.exe` using the certificate.
2. SmartScreen reputation is tied to the certificate's fingerprint. With an EV cert, reputation
   is immediate. With an OV cert, SmartScreen may still warn on the first few downloads until
   reputation is established (typically a few hundred installs from diverse users).
3. Once trusted, users see the signed publisher name in the SmartScreen prompt instead of
   "Unknown Publisher", and eventually the warning disappears entirely.

> **Until a signed release is cut**, users need the More info → Run anyway workaround in §7.1.

---

### 16.4 Summary — per-platform signing secret matrix

| Secret | Platform | Where used |
|--------|----------|-----------|
| `APPLE_CERTIFICATE` | macOS | base64 `.p12` for signing |
| `APPLE_CERTIFICATE_PASSWORD` | macOS | `.p12` export password |
| `APPLE_SIGNING_IDENTITY` | macOS | `Developer ID Application: Name (TEAMID)` |
| `APPLE_ID` | macOS | Apple ID email for notarization |
| `APPLE_PASSWORD` | macOS | App-specific password (not Apple ID password) |
| `APPLE_TEAM_ID` | macOS | 10-char team ID |
| `WINDOWS_CERTIFICATE` | Windows | base64 `.pfx` for signing |
| `WINDOWS_CERTIFICATE_PASSWORD` | Windows | `.pfx` export password |
| `TAURI_SIGNING_PRIVATE_KEY` | Both | Minisign key for in-app auto-update (ADR-0049) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Both | Minisign key password |

**Important:** `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` (ADR-0049,
§7.7 of this guide) are separate from the OS signing credentials. The minisign key signs the
artifact for the in-app update integrity check; the OS credentials establish Gatekeeper /
SmartScreen trust for the installer. Both sets of credentials must be present for a fully
signed, auto-updatable release.

---

## 17. Documentation site (MkDocs Material) {#docs-site}

Synapse v1.0 ships a full MkDocs Material documentation site (R10-6, CLAUDE.md §8 M6 optional).
The site renders all Mermaid diagrams natively and is published to GitHub Pages.

### 17.1 Live site

The public documentation site is available at:

```
https://emanuele-chiummo.github.io/llm-wiki-synapse/
```

The site is published automatically on every push to the `main` branch via the GitHub Pages
deployment workflow. The site is served from the `site/` directory (gitignored).

### 17.2 Local preview

To serve the docs site locally:

```bash
cd backend && .venv/bin/python -m mkdocs serve --config-file ../mkdocs.yml
```

Or use the make target:

```bash
make docs-serve
```

Navigate to `http://127.0.0.1:8000` to preview the site. Changes to files in `docs/` are
reloaded automatically.

### 17.3 Build and verify

To build the site and check for broken links or invalid Mermaid:

```bash
cd backend && .venv/bin/python -m mkdocs build --strict --config-file ../mkdocs.yml
```

The `--strict` flag treats any warning (broken links, invalid config) as an error. This is the
command run by the docs CI gate.

---

## 18. Marker PDF extractor microservice (optional, R12-5) {#marker-microservice}

Marker is a high-quality vision-model PDF extraction engine that uses surya OCR and layout detection to produce rich Markdown output from PDFs, including equations, tables, and figures. It is an optional component — Synapse always falls back to the bundled `pypdf` extractor if the Marker service is unavailable.

### 18.1 Three deployment modes

Choose based on your hardware and use case:

| Mode | Best for | Setup | Speed |
|------|----------|-------|-------|
| **Mac dev (local)** | Development on Apple Silicon | Run `tools/marker-converter/service.py` directly; set `TORCH_DEVICE=mps` | Fast (Metal GPU acceleration) |
| **Server CPU (Docker)** | Production without GPU | `docker compose --profile marker up -d` (CPU-only) | Medium (CPU, single-threaded lock) |
| **TrueNAS GPU (Docker + CUDA)** | Production with RTX 3060 | `docker compose --profile marker up -d` + uncomment GPU block + `TORCH_DEVICE=cuda` | Fast (GPU acceleration) |

### 18.2 Local development (macOS Apple Silicon)

Run the Marker service directly on your Mac using Metal GPU acceleration:

```bash
cd tools/marker-converter

# Create isolated venv (separate from the backend venv)
python3.13 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# Run with Metal GPU (fastest on Apple Silicon)
TORCH_DEVICE=mps ./.venv/bin/python service.py --port 8555
```

First run downloads models (~2 GB) to `~/.cache/huggingface/hub/` (slow once; cached thereafter).

In `.env`:
```env
PDF_EXTRACTOR=marker
MARKER_SERVICE_URL=http://localhost:8555
```

### 18.3 Server deployment — CPU only (no GPU)

For TrueNAS SCALE or any Docker host **without GPU**:

```bash
# Enable the marker profile and start the containerized service
docker compose --profile marker up -d

# Verify it's running
curl http://localhost:8555/health
# {"status":"ok"}
```

In `.env`:
```env
PDF_EXTRACTOR=marker
MARKER_SERVICE_URL=http://marker:8555     # Docker network DNS name
```

The container runs on CPU (shared with backend container). A single lock prevents parallel conversions — one PDF at a time. Typical speed: 3-10 seconds per page depending on PDF complexity.

### 18.4 TrueNAS GPU deployment (RTX 3060 + CUDA)

To use the RTX 3060 GPU on your TrueNAS SCALE host:

1. **Verify nvidia-docker is installed** on TrueNAS (check via TrueNAS Apps or CLI):
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.2.0-runtime-ubuntu22.04 nvidia-smi
   ```
   If this fails, GPU support is not available — fall back to CPU mode (§18.3).

2. **Uncomment the GPU block** in `docker-compose.yml` (under the `marker` service `deploy:` section):
   ```yaml
   deploy:
     resources:
       reservations:
         devices:
           - driver: nvidia
             count: 1
             capabilities: [compute, utility]
   ```

3. **Set TORCH_DEVICE in .env**:
   ```env
   PDF_EXTRACTOR=marker
   MARKER_SERVICE_URL=http://marker:8555
   TORCH_DEVICE=cuda
   ```

4. **Start the stack with the marker profile**:
   ```bash
   docker compose --profile marker up -d
   ```

5. **Verify GPU is active** (inside the marker container):
   ```bash
   docker exec synapse-marker python -c "import torch; print(f'GPU: {torch.cuda.is_available()}')"
   # GPU: True
   ```

Typical GPU speed: 1-3 seconds per page (5-10x faster than CPU). On timeout or GPU memory exhaustion, the backend logs a WARNING and falls back to pypdf automatically.

### 18.5 Model caching (HF_HOME volume)

Marker downloads model weights on first use:
- `torch` and `torchvision` (Pytorch core, ~500 MB)
- `surya-ocr` model (layout detection, ~1.2 GB)
- `marker-pdf` weights (~600 MB)

All are cached in the `marker_models` Docker volume (named volume in `docker-compose.yml`). The volume persists across container restarts — models are downloaded only once.

To clear the cache (e.g., to force a fresh download or free disk space):
```bash
docker volume rm synapse_marker_models
```

Next start will re-download; on slow networks this takes 2-5 minutes.

### 18.6 Healthcheck and timeout

The marker service exposes `GET /health` → `{"status":"ok"}` (200 OK).

Docker Compose healthcheck:
- Interval: 10 s
- Timeout: 5 s
- Start period: **60 s** (generous, accounting for first model download)
- Retries: 3 before marking unhealthy

Backend timeout for `POST /convert`:
- `MARKER_TIMEOUT_SECONDS`: 120 s (default, adjustable in `.env`)
- On timeout, the backend logs a WARNING and falls back to pypdf

A typical PDF conversion takes 3-10 seconds on CPU, 1-3 seconds on GPU. Large scanned documents (500+ pages) may approach the 120 s limit — increase `MARKER_TIMEOUT_SECONDS` if needed.

### 18.7 Disabling Marker

To disable Marker and always use pypdf (the default):
- Leave `PDF_EXTRACTOR=pypdf` in `.env` (or unset; pypdf is the default)
- Do **not** start the marker profile
- The backend will never attempt to reach the Marker service

This is the safest fallback: pypdf is pure Python, always available, and never fails.

### 18.8 Toggling at runtime

To switch PDF extractors without restarting Synapse:
1. Set `PDF_EXTRACTOR=marker` or `PDF_EXTRACTOR=pypdf` in Settings > Advanced (once this UI is added) or directly in `.env`
2. Restart the backend: `docker compose restart synapse-backend`
3. New PDFs use the selected extractor; previously extracted PDFs are unchanged

---

## 19. ServiceNow doc connector (optional external tool) {#servicenow}

The ServiceNow connector is an external Python tool (`tools/marker-converter/`) that
converts ServiceNow documentation PDFs (docs.servicenow.com exports) into structured
Markdown source files suitable for Synapse ingest. It is not part of the Synapse Docker
Compose stack; it runs as a standalone script or optional scheduler daemon on the
developer's machine (or in a sidecar container).

For full setup instructions, see the tool's own README:
`tools/marker-converter/README.md`

### 19.1 One-shot conversion

```bash
cd tools/marker-converter
python3.13 -m venv .venv
./.venv/bin/pip install -r requirements.txt

TORCH_DEVICE=mps ./.venv/bin/python servicenow_connector.py \
    --pdf ~/Downloads/servicenow-itam-enus.pdf \
    --module-code ITAM --module-title "IT Asset Management" \
    --out /path/to/vault/raw/sources
```

The converter splits the PDF by bookmark structure (module → feature → group → section),
converts each section with [Marker](https://github.com/VikParuchuri/marker), and writes
one Markdown source file per section into `raw/sources/servicenow/<module>/<feature>/`.
The Synapse watcher (or `POST /sources/ingest-all`) then ingests these source files via
the standard analyze→generate→validate loop, producing typed, linked wiki pages.

### 19.2 Watch-daemon mode

The `--watch-dir` flag runs the connector as a bounded scheduler daemon: once per tick it
scans the watch directory for new PDFs, converts them, and sleeps until the next tick.
New PDFs dropped into the watch directory are picked up automatically.

```bash
TORCH_DEVICE=mps ./.venv/bin/python servicenow_connector.py \
    --watch-dir ~/Downloads/sn-pdfs \
    --out /path/to/vault/raw/sources \
    --interval-minutes 60 \
    --module-code ITAM --module-title "IT Asset Management"
```

Key behavior:
- State is persisted in `out/.sn_connector_state.json` (SHA-256 keyed). A PDF is never
  re-converted unless the state file is deleted.
- At most 20 PDFs per tick (I7 cap; override with `--max-files`).
- A `launchd` plist template for macOS is provided in `tools/marker-converter/com.synapse.sn-connector.plist.template`. On TrueNAS SCALE, use a cron job or a Docker Compose sidecar with `restart: unless-stopped` instead of launchd.

### 19.3 Integration with Synapse

The converter writes raw source files into `vault/raw/sources/servicenow/`. Synapse
treats these exactly like any other source: the watchdog detects new files and triggers
the normal ingest loop. The source files intentionally carry no `type:` frontmatter — the
LLM assigns valid wiki types (entity, concept, synthesis, etc.) during the analyze step.
Forcing a type from the converter was tried and removed; it produced invalid page types
that broke the wiki type system.

---

## 20. Backup & restore (R8-4, extended 1.9.1 W4) {#backup-restore}

Synapse provides export artifacts that together constitute a full vault backup, PLUS a
built-in scheduled Postgres dump (§12.0) for point-in-time database recovery.
There is no import/restore endpoint in v0.8 — restore is a manual procedure documented
below. A `/import` endpoint is planned for a future sprint.

### 20.1 Downloading the export artifacts

While Synapse is running:

```bash
# 1 — Vault filesystem snapshot (raw/ + wiki/ + purpose.md + schema.md + .obsidian/ JSON)
curl -f http://localhost:8000/export \
     -o synapse-vault-backup-$(date +%Y%m%d).zip

# 2 — Database metadata snapshot (pages, links, edges, ingest_runs, review_items)
curl -f http://localhost:8000/export/data.json \
     -o synapse-data-$(date +%Y%m%d).json

# 3 — Extended metadata snapshot (1.9.1 W4): everything in #2 PLUS conversations
#     (with messages), provider_config, and vault_state
curl -f http://localhost:8000/export/full \
     -o synapse-data-full-$(date +%Y%m%d).json
```

`GET /export` returns a streaming ZIP named `synapse-vault-{vault_id}-{date}.zip`.
`GET /export/data.json` returns a JSON object with top-level keys:
`pages`, `links`, `edges`, `runs`, `review_items`, `exported_at`, `data_version`.
`GET /export/full` (1.9.1 W4) adds `conversations` (each with its `messages`),
`provider_config`, and `vault_state` to the same shape.

**Secrets stay secret:** `provider_config[].api_key_encrypted_b64` in `/export/full` is the
Fernet ciphertext, base64-encoded for JSON transport — it is **never decrypted** by the
export endpoint. Restoring those rows only works on a deployment configured with the
**same** `SYNAPSE_SECRET_KEY` that encrypted them; after a key rotation, re-enter any
UI-supplied provider API keys via Settings instead of restoring the old ciphertext.

Bounds:
- ZIP export is capped at 500 MB uncompressed (returns HTTP 413 if exceeded).
- Only one export may run at a time per vault; a concurrent request returns HTTP 429.

For the database itself, prefer the built-in scheduled `pg_dump` (§12.0) over
`/export/data.json` / `/export/full` as your primary DB backup — the JSON exports are
read-only audit snapshots (no content bodies, no restore tooling reads them directly),
while the `pg_dump -Fc` archives from §12.0 are restorable with `pg_restore` (§20.3).

### 20.2 Restore path A — vault directory only (watcher re-ingest)

Use this path when: the Postgres database was lost but the vault filesystem was preserved,
OR when you want to restore to a new host without migrating the database.

1. Stop the Synapse stack:
   ```bash
   docker compose down
   ```
2. Unzip the vault backup over the existing vault directory (or a fresh one):
   ```bash
   unzip -o synapse-vault-backup-YYYYMMDD.zip -d /path/to/vault
   ```
   The archive layout mirrors the vault directory: `raw/`, `wiki/`, `purpose.md`,
   `schema.md`, `wiki/.obsidian/`.
3. Point `VAULT_PATH` in your `.env` at the restored directory (if changed).
4. Start the stack:
   ```bash
   docker compose up -d
   ```
5. The watchdog detects all files in `raw/sources/` on startup and triggers incremental
   ingest for each one (I1 — the mtime-then-hash gate ensures only genuinely new content
   is re-ingested). Existing `wiki/` pages are indexed as-is.

**What is NOT restored** by this path: Qdrant vector embeddings, Postgres metadata
(pages, links, edges, provider_config rows, conversation history). The re-ingest
recreates metadata and re-embeds content, but conversation history is lost.

### 19.3 Restore path B — full restore with Postgres volume

Use this path when: you have a Postgres volume snapshot (e.g. `docker volume create` +
`docker cp`) in addition to the vault ZIP. This is the fastest restore and preserves
all metadata including conversation history.

1. Stop the Synapse stack:
   ```bash
   docker compose down
   ```
2. Restore the Postgres data volume from your snapshot. The exact command depends on
   your backup mechanism (e.g. `pg_restore`, volume copy, TrueNAS dataset snapshot).
   Example for a logical dump:
   ```bash
   docker compose run --rm postgres \
       psql -U synapse -d postgres -c "DROP DATABASE IF EXISTS synapse; CREATE DATABASE synapse;"
   docker compose run --rm postgres \
       pg_restore -U synapse -d synapse /backup/synapse-YYYYMMDD.dump
   ```
3. Restore the vault filesystem (step 2 of Path A above).
4. Start the stack:
   ```bash
   docker compose up -d
   ```
5. The watcher starts but the mtime-then-hash gate (I1) will find all files already
   indexed (content_hash matches) and skip re-ingest. The graph coords and embeddings
   are already in Postgres and Qdrant respectively.

**Note:** The `data.json` artifact (`GET /export/data.json`) is a read-only audit
snapshot — it is NOT used as input to either restore path. It is useful for verifying
the vault state (page count, data_version) before and after a restore, or for external
tooling (scripts, dashboards) that need a structured view of the vault metadata without
a live Postgres connection.

---

## 21. References

- `CLAUDE.md` — project context, invariants (I1–I9), and feature inventory
- `docs/er/schema.mmd` — ER diagram (auto-generated by `make er`)
- `docs/api/openapi.json` — API reference (auto-generated by `make openapi`)
- `docs/adr/` — Architecture Decision Records (index in `docs/adr/index.md`; ADR-0037 Lint,
  ADR-0038 Web Clipper, ADR-0039 Tauri v2 shell, ADR-0047 Desktop Connect gate, ADR-0049 Desktop
  auto-update, ADR-0051 Pluggable PDF extractor seam, ADR-0052 Shared Bearer token auth,
  ADR-0075 Deployment trust mode and health boundary)
- `docs/adr/0039-tauri-v2-desktop-shell.md` — Tauri v2 desktop shell scaffold and CI
- `docs/adr/0047-desktop-runtime-server-url-and-connect-gate.md` — runtime server URL binding, Connect screen, CORS extension (§7 of this guide)
- `docs/adr/0049-desktop-auto-update-github-releases.md` — unified v* release channel, minisign auto-update, key-loss caveat (§7.7 of this guide)
- `docs/adr/0052-auth-token-model.md` — shared Bearer token (`SYNAPSE_AUTH_TOKEN`), middleware
  ordering and client contract (§15 of this guide)
- `docs/adr/0075-deployment-mode-auth-health-boundary.md` — `local`/`server` trust boundary,
  fail-closed server validation and protected detailed diagnostics (§3.4 and §15 of this guide)
- `tools/marker-converter/README.md` — Marker PDF microservice + ServiceNow doc connector full setup and scheduler daemon
- `tools/whisper-service/README.md` — Whisper AV transcription microservice setup (required when `AV_TRANSCRIPTION_ENABLED=true`)
- §17 (this guide) — Backup & restore: `GET /export` ZIP + `GET /export/data.json` metadata
- §14 (this guide) — Code signing and notarization: macOS Developer ID + notarization, Windows OV/EV cert, GitHub Actions secrets matrix
- §15 (this guide) — Documentation site: MkDocs Material, `make docs-serve`, GitHub Pages URL
- `docs/USER.md` — end-user guide
