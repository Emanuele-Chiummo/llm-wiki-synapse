# Synapse

Self-organising AI wiki from raw documents (Karpathy LLM-Wiki pattern).

This TrueNAS app deploys **PostgreSQL + Qdrant + the Synapse backend + frontend**
as four containers. It reuses services already running on your NAS — **Ollama /
bge-m3** and **SearXNG** — which are referenced by URL, not containerized.

## Before installing

1. Create the vault host folder and make it writable by uid 1000:
   ```bash
   mkdir -p /mnt/<pool>/APP_Configs/Synapse/vault
   chown -R 1000:1000 /mnt/<pool>/APP_Configs/Synapse/vault
   ```
   The backend populates `raw/`, `wiki/`, `schema.md`, `purpose.md` on first start.
2. Set **Ollama base URL** / **Embedding endpoint** to your NAS IP (e.g.
   `http://192.168.1.107:11434`).

## Inference authentication

- **Local (Ollama)** — leave both auth fields empty; configure a `local` provider in Settings.
- **API (pay-per-token)** — set *Anthropic API key*.
- **Subscription (CLI)** — run `claude setup-token` on your machine and paste the
  `sk-ant-oat01-...` token into *Claude Code OAuth token*. Leave the API key EMPTY
  (an API key, even blank, forces per-token billing).

After first start, create a `provider_config` row in **Settings → Provider** (it
persists in Postgres). The auth secret itself lives only in this app's config, never in the DB.

## Access

- Web UI: `http://<nas-ip>:<web port>` (default 5173)
- API / Swagger: `http://<nas-ip>:<api port>/docs` (default 8000)
