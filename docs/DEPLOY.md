# Synapse Deployment Guide

<!-- Generated: v0.4 sprint 4 | 2026-06-28 -->

> Target: TrueNAS SCALE 25.10 "Goldeye" + Docker Compose
> Version: v0.4 draft — covers the shipped M4 feature set (3-panel UI, chat, provider
> selector, graph viewer). Promoted from v0.1 initial draft.
> Status: v0.4 DRAFT — test locally before TrueNAS deploy

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

before launching uvicorn. This creates all tables (migrations 0001–0007), seeds the
`vault_state` row, and inserts the initial `provider_config` rows using the
`DEFAULT_MODEL_ID` env var. You do not need to run migrations manually.

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

## 5. TrueNAS SCALE deployment

### 5.1 Deploy via SSH

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

### 5.2 Vault bind mount

The `vault/` directory in the repo root is bind-mounted at `/vault` inside the
`synapse-backend` container (see `docker-compose.yml`). On TrueNAS you may want to
bind from the ZFS dataset instead:

Edit `docker-compose.yml` volumes section:

```yaml
volumes:
  - /mnt/pool/synapse/vault:/vault
```

### 5.3 Networking

**Tailscale (internal):** all TrueNAS services are on the same Tailscale mesh. Access
the backend API at `http://truenas-node-ip:8000` or `http://truenas.local:8000`.

**Cloudflare Tunnel (public HTTPS, optional):**

1. Configure a Cloudflare Tunnel on TrueNAS (one-time, at host level).
2. Add an ingress rule: `synapse.yourdomain.com` → `http://localhost:8000`.
3. Expose the frontend origin in `CORS_ALLOW_ORIGINS`.

---

## 6. Useful make targets

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

## 7. Backup strategy

### 7.1 Postgres

```bash
# Dump from inside the container
docker compose exec -T postgres pg_dump \
  -U synapse -d synapse \
  | gzip > /mnt/pool/synapse/postgres-backups/synapse-db-$(date +%Y%m%d-%H%M%S).sql.gz
```

Automate with a TrueNAS periodic task or cron job.

### 7.2 Vault filesystem

The `vault/` directory contains the raw documents and AI-generated wiki pages. Back
it up with a ZFS snapshot:

```bash
zfs snapshot pool/synapse@backup-$(date +%Y%m%d)
```

---

## 8. CI/CD

### 8.1 CI stages

| Stage | Trigger | Required | Purpose |
|-------|---------|----------|---------|
| `lint` | push / PR | Yes | ruff + black checks |
| `typecheck` | push / PR | Yes | mypy strict mode |
| `unit` | push / PR | Yes | pytest (no external services) |
| `docs` | push / PR | Yes | ER + OpenAPI drift check; mmdc Mermaid render |
| `integration` | manual | Optional | docker-compose E2E (requires live TrueNAS services) |

### 8.2 Docs gate

The `docs` stage runs `make er` and `make openapi`, then diffs the output against the
committed files. A mismatch fails the PR. Fix it with:

```bash
make er
make openapi
git add docs/er/schema.mmd docs/api/openapi.json
git commit -m "docs: refresh ER and OpenAPI [I8]"
```

---

## 9. Troubleshooting

### 9.1 "connection refused" on EMBEDDING_URL or QDRANT_URL

Cause: the external service is not running or the Docker container cannot reach the
host network.

```bash
# Verify the external service is up
curl -s http://100.x.x.x:11434/api/tags | jq .
curl -s http://100.x.x.x:6333/health

# Verify docker can reach the host
docker run --rm alpine ping -c 1 host.docker.internal
```

### 9.2 EMBEDDING_DIM mismatch

Cause: the `EMBEDDING_DIM` env var does not match the actual output of the bge-m3
model running in Ollama.

```bash
curl -s -X POST http://100.x.x.x:11434/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "bge-m3", "prompt": "test"}' | jq '.embedding | length'
# Returns the actual dimension — update EMBEDDING_DIM to match
docker compose restart synapse-backend
```

### 9.3 Pre-existing files are not ingested on startup

By design (incremental index — the watcher picks up new and modified files only).
To ingest files that existed before Synapse started, trigger a run manually:

```bash
curl -X POST http://localhost:8000/ingest/trigger
```

Or use the **Run Ingest** button in the Ingest section of the web UI.

### 9.4 No provider_config row — hard error on ingest or chat

If the application logs "no provider_config found for scope=global", insert at least
one global row (see §4). A missing global row is never silently ignored.

### 9.5 Chat provider returns NotImplementedError (CLI backend)

`CliAgentProvider.chat()` is not yet implemented (M5 work item). Switch to the Local
or API provider for chat. Ingest with CLI works normally.

---

## 10. References

- `CLAUDE.md` — project context, invariants (I1–I9), and feature inventory
- `docs/er/schema.mmd` — ER diagram (auto-generated by `make er`)
- `docs/api/openapi.json` — API reference (auto-generated by `make openapi`)
- `docs/adr/` — Architecture Decision Records (ADR-0001 through ADR-0019)
- `docs/USER.md` — end-user guide
