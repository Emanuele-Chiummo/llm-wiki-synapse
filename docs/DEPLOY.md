# Synapse v0.1 Deployment Guide

> Target: TrueNAS SCALE 25.10 + Docker Compose  
> Last updated: 2026-06-28  
> Sprint: v0.1 — Walking skeleton  
> Status: Initial draft — test locally before TrueNAS deploy

---

## 1. Prerequisites

### 1.1 TrueNAS SCALE services (already running — I9)

The following must be **already running** on TrueNAS. Synapse does NOT containerize them:

| Service | Purpose | Port | Tailscale IP | Notes |
|---------|---------|------|--------------|-------|
| **Ollama** | Local LLM inference (RTX 3060) | 11434 | 100.x.x.x | CPU only in v0.1 (watcher + ingest are CPU-bound) |
| **bge-m3 embeddings** | Via Ollama `/api/embeddings` | 11434 | 100.x.x.x | Dimension: 1024 (configurable) |
| **Qdrant** | Vector store (bge-m3 embeddings) | 6333 | 100.x.x.x | Used by ALL vaults; data persists in TrueNAS storage |
| **SearXNG** | Web search backend (v0.2+) | 8888 | 100.x.x.x | Optional; required for F10 (Deep Research) |

**Verify services are up:**
```bash
# From TrueNAS or any Tailscale node
curl -s http://100.x.x.x:11434/api/tags | jq '.models'  # List Ollama models
curl -s http://100.x.x.x:6333/health               # Qdrant health
```

### 1.2 Docker & Docker Compose

- TrueNAS SCALE includes Docker (via App or Kubernetes).
- Ensure `docker` and `docker compose` are available:
  ```bash
  docker --version
  docker compose version
  ```

### 1.3 Volumes & storage paths

On TrueNAS, create the following storage pool/dataset paths:

```bash
/mnt/pool/synapse/                    # Synapse data root
├── vault/                            # Bind-mounted to container
│   ├── raw/sources/                  # User-uploaded documents
│   ├── raw/assets/                   # Associated assets
│   ├── wiki/                         # Synapse-generated output (valid Obsidian vault)
│   ├── schema.md                     # Vault schema rules
│   ├── purpose.md                    # Vault context
│   └── .obsidian/                    # Auto-generated Obsidian config
└── postgres-backups/                 # Daily Postgres dumps (external script)
```

---

## 2. Local setup (developer machine)

### 2.1 Clone and prepare

```bash
git clone https://github.com/<owner>/synapse.git
cd synapse
git checkout sprint/v0.1

# Copy env template
cp .env.example .env

# Edit .env for your environment
# For local dev: DATABASE_URL=postgresql://..., VAULT_PATH=../vault
# For docker: DATABASE_URL=postgresql://synapse:synapse@postgres:5432/synapse, VAULT_PATH=/vault
```

### 2.2 Environment variables (.env)

**Critical vars for v0.1:**

| Var | Example | Notes |
|-----|---------|-------|
| `DATABASE_URL` | `postgresql+asyncpg://synapse:synapse@postgres:5432/synapse` | Docker: use `postgres` service; local dev: `localhost` |
| `QDRANT_URL` | `http://host.docker.internal:6333` | Docker: use `host.docker.internal`; local: `100.x.x.x` (Tailscale) or `localhost` |
| `EMBEDDING_URL` | `http://host.docker.internal:11434/api/embeddings` | Same as Qdrant |
| `EMBEDDING_DIM` | `1024` | Must match bge-m3 actual output; verify with bge-m3 service |
| `EMBEDDING_MODEL` | `bge-m3` | Model name passed in embedding requests |
| `VAULT_ID` | `default` | Logical vault identifier |
| `VAULT_PATH` | `/vault` (docker) or `../vault` (local) | Mounted path or relative |

**Examples:**

- **Docker on TrueNAS:**
  ```env
  DATABASE_URL=postgresql+asyncpg://synapse:synapse@postgres:5432/synapse
  QDRANT_URL=http://host.docker.internal:6333
  EMBEDDING_URL=http://host.docker.internal:11434/api/embeddings
  EMBEDDING_DIM=1024
  EMBEDDING_MODEL=bge-m3
  VAULT_ID=default
  VAULT_PATH=/vault
  ```

- **Local dev (macOS/Linux, Tailscale to TrueNAS):**
  ```env
  DATABASE_URL=postgresql+asyncpg://synapse:synapse@localhost:5432/synapse
  QDRANT_URL=http://100.x.x.x:6333
  EMBEDDING_URL=http://100.x.x.x:11434/api/embeddings
  EMBEDDING_DIM=1024
  EMBEDDING_MODEL=bge-m3
  VAULT_ID=default
  VAULT_PATH=../vault
  ```

### 2.3 Build and start (docker-compose)

```bash
# Start Postgres and Synapse backend
docker compose up -d

# Logs
docker compose logs -f synapse-backend

# Verify /status endpoint
curl http://localhost:8000/status
```

**Expected output:**
```json
{
  "vault_id": "default",
  "data_version": 0,
  "started_at": "2026-06-28T10:30:00Z",
  "uptime_seconds": 42
}
```

### 2.4 Local testing (without docker)

```bash
# Install venv and backend dependencies
cd backend
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e .
pip install -e ".[dev]"

# Run unit tests (service-free, no Postgres/Qdrant needed)
make test

# Lint + type check
make lint
make typecheck

# Generate docs (D2, D4)
make er       # → docs/er/schema.mmd
make openapi  # → docs/api/openapi.json
```

---

## 3. TrueNAS deployment

### 3.1 Prepare TrueNAS storage

```bash
# SSH into TrueNAS
ssh admin@truenas.local

# Create datasets
zfs create pool/synapse
zfs create pool/synapse/vault
zfs create pool/synapse/postgres-backups

# Set permissions (Docker runs as root via root user, but best practice: explicit 755)
chmod 755 /mnt/pool/synapse
chmod 755 /mnt/pool/synapse/vault
```

### 3.2 Deploy via `docker compose` on TrueNAS

**Option A: Via TrueNAS App Catalog (Recommended)**

1. TrueNAS UI → **Apps** → **Discover** → search "Docker Compose" (or create custom app)
2. Upload `docker-compose.yml` and `.env` from the repo
3. Configure:
   - Container image path: `/path/to/backend/Dockerfile`
   - Environment: load from `.env`
   - Volumes: `/mnt/pool/synapse/vault` → `/vault`
4. Deploy and monitor logs

**Option B: Via TrueNAS CLI / SSH**

```bash
# SSH into TrueNAS
ssh admin@truenas.local

# Clone or copy repo
git clone https://github.com/<owner>/synapse.git /mnt/pool/synapse/synapse-repo
cd /mnt/pool/synapse/synapse-repo

# Prepare .env (customize for TrueNAS paths)
cat > .env <<EOF
DATABASE_URL=postgresql+asyncpg://synapse:synapse@postgres:5432/synapse
QDRANT_URL=http://host.docker.internal:6333
EMBEDDING_URL=http://host.docker.internal:11434/api/embeddings
EMBEDDING_DIM=1024
VAULT_ID=default
VAULT_PATH=/vault
EOF

# Start services
docker compose up -d

# Verify
docker compose logs -f synapse-backend

# Test /status
curl http://localhost:8000/status
```

### 3.3 Networking: Tailscale + Cloudflare Tunnel

**Tailscale:** The RTX 3060 host and all TrueNAS services are on the same Tailscale mesh. Synapse backend is accessible internally via:
- Container: `host.docker.internal` or TrueNAS node IP
- External (Tailscale): `http://truenas.local:8000` or `http://100.x.x.x:8000`

**Cloudflare Tunnel (optional, for public HTTPS):**

1. Configure Cloudflare Tunnel on TrueNAS (once, at host level)
2. Add route: `synapse.yourdomain.com → http://localhost:8000`
3. Access: `https://synapse.yourdomain.com/status`

---

## 4. CI/CD (GitHub Actions)

### 4.1 CI stages (`.github/workflows/ci.yml`)

| Stage | Trigger | Status | Purpose |
|-------|---------|--------|---------|
| **lint** | push/PR | Required | ruff + black checks |
| **typecheck** | push/PR | Required | mypy strict mode |
| **unit** | push/PR | Required | pytest (infra-free tests) |
| **docs** | push/PR | Required | ER + OpenAPI drift check; Mermaid validation |
| **integration** | manual (future) | Optional | docker-compose E2E (requires TrueNAS services) |

**Local check before push:**
```bash
make lint
make typecheck
make test
make er
make openapi
```

### 4.2 Docs gate (I8)

The **docs** stage runs `make er` and `make openapi`, then compares against committed artifacts:
- `docs/er/schema.mmd` — auto-generated from SQLAlchemy models
- `docs/api/openapi.json` — auto-generated from FastAPI app

**If mismatch:** PR fails. Fix with:
```bash
make er
make openapi
git add docs/er/schema.mmd docs/api/openapi.json
git commit -m "docs: refresh ER and OpenAPI [I8]"
```

---

## 5. Backup strategy

### 5.1 Postgres data

**Daily backup (via cron or TrueNAS task):**
```bash
# Run inside docker container
docker compose exec -T postgres pg_dump \
  -U synapse -d synapse \
  > /backups/synapse-db-$(date +%Y%m%d-%H%M%S).sql.gz

# Or via Make target (future)
make backup
```

### 5.2 Vault data

Vault is persisted on TrueNAS filesystem (`/mnt/pool/synapse/vault/`). Use TrueNAS ZFS snapshots:
```bash
zfs snapshot pool/synapse@backup-$(date +%Y%m%d)
```

---

## 6. Troubleshooting

### 6.1 "connection refused" on EMBEDDING_URL or QDRANT_URL

**Cause:** External services not running or network unreachable.

**Fix:**
```bash
# Verify services on TrueNAS
curl -s http://100.x.x.x:11434/api/tags | jq .
curl -s http://100.x.x.x:6333/health

# Verify docker can reach host
docker run --rm alpine ping -c 1 host.docker.internal
```

### 6.2 "EMBEDDING_DIM mismatch" on startup

**Cause:** Configured `EMBEDDING_DIM` doesn't match bge-m3 output.

**Fix:**
```bash
# Query bge-m3 directly
curl -s -X POST http://100.x.x.x:11434/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "bge-m3", "prompt": "test"}' | jq '.embedding | length'

# Update .env
EMBEDDING_DIM=<actual_length>

# Restart
docker compose restart synapse-backend
```

### 6.3 Watcher not detecting files

**Cause:** Pre-existing files in `vault/raw/sources/` are not auto-indexed (I1, AQ-3).

**Expected behavior:** New/modified files trigger ingest; pre-existing files are not scanned.

**To ingest existing files:** Use `POST /ingest/trigger` endpoint.

---

## 7. Next steps (v0.2+)

- [ ] InferenceProvider backends (F17): Ollama, Anthropic API, Claude Agent SDK
- [ ] Ingest orchestrator loop: analyze → generate → validate → retry
- [ ] Multi-vault support (currently VAULT_ID = "default")
- [ ] Persistent async ingest queue (task_id tracking)
- [ ] Web UI (React + Vite)
- [ ] Graph visualization (sigma.js + FA2 layout)

---

## 8. References

- **CLAUDE.md** — project context and invariants (I1–I9)
- **docs/sprints/v0.1-architecture.md** — detailed design decisions (AQ table)
- **docs/adr/*** — Architecture Decision Records
- **docs/er/schema.mmd** — ER diagram (auto-generated by `make er`)
- **docs/api/openapi.json** — API reference (auto-generated by `make openapi`)
