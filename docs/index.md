# Synapse

<div align="center" markdown>

![Synapse logo](assets/synapse-appicon.svg){ width="110" }

**Self-hosted AI wiki that organizes itself.**

Drop in raw documents — Synapse reads them, writes interlinked wiki pages,
builds a weighted knowledge graph, and lets you chat with your own knowledge base, with citations.

</div>

!!! tip "What's new in v2.0.0"
    The v2.0 "one engine" release removes the legacy JSON ingest pipeline (the block-based
    pipeline is now the sole ingest engine), dissolves the compatibility facades introduced in
    1.7.0, and ships a stable JSON error envelope as a public API contract. See
    **[v2.0.0 release notes](release-notes/v2.0.0.md)** for full details and upgrade
    instructions, or browse the **[full changelog](https://github.com/Emanuele-Chiummo/llm-wiki-synapse/blob/main/CHANGELOG.md)**.

---

## What it does

| | |
|---|---|
| 📥 **Ingest anything** | Markdown, PDF, DOCX, PPTX, XLSX — via file watcher, upload, scheduled folder import, or the Chrome web clipper. An LLM analyzes each source and writes typed, interlinked wiki pages. |
| 🕸️ **Knowledge graph** | 4-signal edge weighting, server-side ForceAtlas2 layout, Louvain communities, WebGL rendering (sigma.js). Never freezes your browser. |
| 💬 **Chat with citations** | 4-phase retrieval (vector → graph expansion → token budget → assembly) over your wiki, streamed answers with `[n]` citations, save-to-wiki. |
| 🔍 **Deep research** | Bounded agentic loop over SearXNG web search → synthesized, cited pages, auto-ingested. |
| 🧑‍⚖️ **Human in the loop** | Review queue for AI proposals, bounded lint-fix loop with human-gated apply, cascade delete with dry-run. |
| 🔌 **Bring your own AI** | Pluggable inference provider: **Local** (Ollama), **API** (Anthropic / OpenAI-compatible), **CLI** (claude-agent-sdk). Switch per vault or per operation. |
| 📦 **Obsidian-compatible** | The `wiki/` folder is a valid Obsidian vault: YAML frontmatter, `[[wikilinks]]`, auto-generated `.obsidian/`. |
| 🖥️ **Desktop app** | Native macOS/Windows app (Tauri v2, ~6 MB) with auto-update from GitHub Releases. Point it at your server and go. |

---

## Quick start (server)

```bash
git clone https://github.com/Emanuele-Chiummo/llm-wiki-synapse.git
cd llm-wiki-synapse
cp .env.example .env        # configure DB, Ollama/Qdrant endpoints, provider
docker compose up -d
```

Open `http://localhost:8000` (API) — serve the frontend with `make dev` or any static host.
Requires: Docker, plus the services you already run — Ollama (inference/embeddings, bge-m3), Qdrant (vectors), optionally SearXNG (web search).

---

## Desktop app

Download the latest **`.dmg` (macOS)** or **`.exe` (Windows)** from [Releases](https://github.com/Emanuele-Chiummo/llm-wiki-synapse/releases/latest).
First launch: enter your backend URL (local servers are auto-detected). Builds are unsigned for now — macOS: right-click → Open; Windows: "More info" → "Run anyway". The app checks GitHub for updates at startup and offers to install them.

---

## Stack

FastAPI · SQLAlchemy 2 · PostgreSQL 16 · Qdrant · bge-m3 — React 19 · Vite · CodeMirror 6 · sigma.js · Zustand — Tauri v2.

---

## Resources

- **[User Guide](USER.md)** — complete walkthrough of all features
- **[Deploy Guide](DEPLOY.md)** — TrueNAS SCALE setup, environment config, troubleshooting
- **[Architecture](architecture/index.md)** — C4 diagrams (system context, containers, components)
- **[ADRs](adr/index.md)** — Architecture Decision Records documenting design choices
- **[API Reference](api/index.md)** — OpenAPI specification of all endpoints
- **[Release Notes](release-notes/v2.0.0.md)** — per-release changelogs from v1.2 onward
- **[Roadmap](reference/ROADMAP-v1.3-v2.0.md)** — v1.3 → v2.0 feature roadmap and development status

---

## License

[MIT](https://github.com/Emanuele-Chiummo/llm-wiki-synapse/blob/main/LICENSE) © 2026 Emanuele Chiummo

---

**Based on the [LLM Wiki pattern by Andrej Karpathy](https://karpathy.ai/llm_wiki) ([nashsu/llm_wiki](https://github.com/nashsu/llm_wiki)), re-engineered for performance and self-hosting.**
