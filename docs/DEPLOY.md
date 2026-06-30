# Synapse Deployment Guide

<!-- Generated: v0.5-ADR-0033 | 2026-06-30 -->

> Target: TrueNAS SCALE 25.10 "Goldeye" + Docker Compose
> Version: v0.5 — covers the shipped M5 feature set (M4 features + Deep Research, Review
> Queue, Multi-format ingest, Cascade Delete, Remote MCP, Embeddings toggle).
> Status: v0.5 DRAFT — test locally before TrueNAS deploy

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

### 1.3 Vault storage paths

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
| `EMBEDDINGS_ENABLED` | `true` | No | Set to `false` to disable bge-m3 vectorization and Qdrant entirely. Ingest still runs (Postgres metadata + links only); retrieval and `/search` degrade to lexical Postgres keyword search. Startup skips the embedding probe — Synapse starts even when Qdrant and Ollama embeddings are unreachable. (ADR-0030) |
| `EMBEDDING_FORMAT` | `ollama` | No | Request/response adapter for the embedding service: `ollama` (default — `{"prompt": ...}` → `{"embedding": [...]}`) or `openai` (`{"input": ...}` → `{"data":[{"embedding":[...]}]}`). Set to `openai` when `EMBEDDING_URL` points at an OpenAI-compatible endpoint (e.g. a hosted embeddings API). (ADR-0031) |
| `EMBEDDING_API_KEY` | *(none)* | No | Bearer token for the embedding service. When set, every embedding request includes `Authorization: Bearer <key>`. Leave unset for the local bge-m3/Ollama service (no auth). Never logged or returned by any endpoint. (ADR-0031) |
| `MCP_AUTH_TOKEN` | *(none)* | No | Static bearer token for the remote MCP HTTP surface at `/mcp/server`. When set, the HTTP surface is mounted and requires `Authorization: Bearer <token>` on every request. When **unset**, `/mcp/server` is not mounted (404) — fail-closed. The stdio entry (`python -m app.mcp.server`) is unaffected by this variable. Never logged or returned by any endpoint. (ADR-0029, see §5) |
| `MCP_REMOTE_WRITE_ENABLED` | `false` | No | When `true`, the `write_page` tool is also exposed on the HTTP MCP surface (still bearer-gated by `MCP_AUTH_TOKEN`). Default `false`: only `search_wiki`, `get_page`, `list_pages` are reachable remotely. The stdio path always has all four tools regardless of this setting. (ADR-0029 §2.3) |
| `MCP_TRUSTED_PROXIES` | *(empty)* | No | Comma-separated list of trusted reverse-proxy IP addresses (e.g. `127.0.0.1,::1`). When set, the `X-Forwarded-For` header from listed IPs is honoured to determine the real client IP for the allow-without-token public/private classification. Leave empty (the default) unless you run a local reverse proxy in front of Synapse. The Cloudflare header check (`CF-Connecting-IP`/`CF-Ray`) is independent of this setting. (ADR-0033) |
| `BACKEND_PROXY_TARGET` | `http://localhost:8000` | No | **Dev only (Vite proxy, server-side).** The URL the Vite dev server proxies API calls to. Set to `http://synapse-backend:8000` in `docker-compose.dev.yml` so the Vite process (inside the container) can reach the backend over the Docker network. This variable is intentionally NOT prefixed `VITE_` — it is never inlined into the browser bundle. Browser clients always use a relative base (`""`) by default. (ADR-0028) |

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

### 3.4 Open the frontend

The frontend is served by the Vite dev server (development) or a static file server
(production build). In v0.4 development mode:

```bash
cd frontend
npm install
npm run dev
```

Navigate to `http://localhost:5173`. The three-panel shell should load with the
navigation rail on the left and the knowledge graph in the center.

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

The scan is **non-recursive**: only files directly inside `source_dir` are imported;
subdirectories are not traversed. Recursive scanning is a planned future opt-in.

As of v0.5 (M5), `.md`, `.txt`, `.markdown`, `.pdf`, `.docx`, `.pptx`, and `.xlsx`
files are imported (F12, ADR-0025). Binary files are converted to companion `.extracted.md`
files automatically before ingest. Images and audio/video are placeholder-only (deferred to
M6). Other file types are silently skipped.

---

## 7. TrueNAS SCALE deployment

### 7.1 Deploy via SSH

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

### 7.2 Vault bind mount

The `vault/` directory in the repo root is bind-mounted at `/vault` inside the
`synapse-backend` container (see `docker-compose.yml`). On TrueNAS you may want to
bind from the ZFS dataset instead:

Edit `docker-compose.yml` volumes section:

```yaml
volumes:
  - /mnt/pool/synapse/vault:/vault
```

### 7.3 Networking

**Tailscale (internal):** all TrueNAS services are on the same Tailscale mesh. Access
the backend API at `http://truenas-node-ip:8000` or `http://truenas.local:8000`.

**Cloudflare Tunnel (public HTTPS, optional):**

1. Configure a Cloudflare Tunnel on TrueNAS (one-time, at host level).
2. Add an ingress rule: `synapse.yourdomain.com` → `http://localhost:8000`.
3. Expose the frontend origin in `CORS_ALLOW_ORIGINS`.

---

## 8. Useful make targets

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

## 9. Backup strategy

### 9.1 Postgres

```bash
# Dump from inside the container
docker compose exec -T postgres pg_dump \
  -U synapse -d synapse \
  | gzip > /mnt/pool/synapse/postgres-backups/synapse-db-$(date +%Y%m%d-%H%M%S).sql.gz
```

Automate with a TrueNAS periodic task or cron job.

### 9.2 Vault filesystem

The `vault/` directory contains the raw documents and AI-generated wiki pages. Back
it up with a ZFS snapshot:

```bash
zfs snapshot pool/synapse@backup-$(date +%Y%m%d)
```

---

## 10. CI/CD

### 10.1 CI stages

| Stage | Trigger | Required | Purpose |
|-------|---------|----------|---------|
| `lint` | push / PR | Yes | ruff + black checks |
| `typecheck` | push / PR | Yes | mypy strict mode |
| `unit` | push / PR | Yes | pytest (no external services) |
| `docs` | push / PR | Yes | ER + OpenAPI drift check; mmdc Mermaid render |
| `integration` | manual | Optional | docker-compose E2E (requires live TrueNAS services) |

### 10.2 Docs gate

The `docs` stage runs `make er` and `make openapi`, then diffs the output against the
committed files. A mismatch fails the PR. Fix it with:

```bash
make er
make openapi
git add docs/er/schema.mmd docs/api/openapi.json
git commit -m "docs: refresh ER and OpenAPI [I8]"
```

---

## 11. Troubleshooting

### 11.1 "connection refused" on EMBEDDING_URL or QDRANT_URL

Cause: the external service is not running or the Docker container cannot reach the
host network.

```bash
# Verify the external service is up
curl -s http://100.x.x.x:11434/api/tags | jq .
curl -s http://100.x.x.x:6333/health

# Verify docker can reach the host
docker run --rm alpine ping -c 1 host.docker.internal
```

### 11.2 EMBEDDING_DIM mismatch

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

### 11.3 Pre-existing files are not ingested on startup

By design (incremental index — the watcher picks up new and modified files only).
To ingest files that existed before Synapse started, trigger a run manually:

```bash
curl -X POST http://localhost:8000/ingest/trigger
```

Or use the **Run Ingest** button in the Ingest section of the web UI.

### 11.4 No provider_config row — hard error on ingest or chat

If the application logs "no provider_config found for scope=global", insert at least
one global row (see §4). A missing global row is never silently ignored.

### 11.5 Chat provider returns an error with CLI backend

`CliAgentProvider.chat()` is implemented in v0.5 as delegated streaming chat (bounded
by `CHAT_AGENT_MAX_TURNS`, `token_budget`, and `timeout_seconds`). If it errors, check
that `ANTHROPIC_API_KEY` is set (the CLI backend requires it), and that the model ID in
`provider_config` is a valid Claude model name. Ingest with CLI works independently.

### 11.6 Scheduled import: last_status="dir_missing"

The import schedule is enabled and a `source_dir` is configured, but scans report
`dir_missing`.

Cause: the path is not visible inside the container. The backend can only see mounted
paths.

Fix:
1. Verify the volume mount is in `docker-compose.yml` (see §5.2).
2. Restart the stack: `docker compose down && docker compose up -d`.
3. Verify the path exists inside the container:

```bash
docker compose exec synapse-backend ls /import
```

4. In Settings → Automatic import, confirm the `source_dir` field shows the container
   path (e.g. `/import`), not a host path.

### 11.7 Uploaded file is rejected with 415

Cause: the file extension is not in the accepted list.

As of v0.5 (M5), accepted formats are: `.md`, `.txt`, `.markdown`, `.pdf`, `.docx`,
`.pptx`, `.xlsx` (F12, ADR-0025). Images and audio/video are not yet supported; they
return 415 with a message naming the planned M6 support. The 415 response body names
the extension and the acceptance list explicitly.

---

## 12. References

- `CLAUDE.md` — project context, invariants (I1–I9), and feature inventory
- `docs/er/schema.mmd` — ER diagram (auto-generated by `make er`)
- `docs/api/openapi.json` — API reference (auto-generated by `make openapi`)
- `docs/adr/` — Architecture Decision Records (ADR-0001 through ADR-0033; index in `docs/adr/README.md`)
- `docs/USER.md` — end-user guide
