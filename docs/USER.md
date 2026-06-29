# Synapse User Guide

<!-- Generated: v0.4 M4-HARD | 2026-06-29 -->

> Version: v0.4 (M4 — "Usable & fluid")
> Language toggle: English / Italian available in Settings.

---

## What is Synapse?

Synapse is a self-hosted web service that turns a folder of raw documents into a
self-organizing knowledge wiki. Drop a file into `vault/raw/sources/`, and Synapse
will analyze it with a configurable AI provider, create structured wiki pages in
`vault/wiki/`, link them to related concepts, and lay out the whole knowledge graph
for you to explore.

The design follows the Karpathy LLM Wiki pattern: the AI maintains the wiki, and you
curate it. The `wiki/` folder is a valid Obsidian vault you can open directly in the
Obsidian app.

---

## The core journey

1. **Open the app** — you land on the Chat section by default. Ask a question or
   navigate to another section using the labeled rail on the left.
2. **Drop a document** into `vault/raw/sources/` (or use the upload zone in the
   Sources section).
3. **Watch the graph grow** — the knowledge graph updates automatically as pages are
   created.
4. **Inspect a page** — click any node on the graph or row in the Wiki tree to read
   its metadata and relationships.
5. **Chat with your wiki** — ask questions in the Chat section; answers stream in
   token by token.
6. **Configure your provider** — add, edit, or delete inference providers from
   Settings > LLM Models; select the active one from the header dropdown.

---

## The interface

Synapse uses a dark-themed shell. A labeled navigation rail on the left lets you switch
between sections without a page reload.

![3-panel shell](screens/shell-3panel.png)

### Navigation rail

The leftmost strip is approximately 72 px wide. Each item shows an icon and a
persistent text label below it. The active section is highlighted with a rounded
rectangle that encloses both icon and label.

The rail contains five interactive items from top to bottom:

| Label | Section |
|-------|---------|
| **Chat** | Multi-conversation streaming chat (default on first load) |
| **Wiki** | File tree + knowledge graph + page inspector |
| **Sources** | Ingest activity history, upload zone, Run Ingest button |
| **Graph** | Full-bleed sigma knowledge graph |
| **Settings** (pinned at bottom) | LLM providers, context window, language, maintenance |

The Search, Lint, Review, and Deep Search sections are coming in M5; they do not appear
in the M4 rail.

The vault name, data version, and active provider appear in the status bar at the
bottom of every section.

---

### Wiki section

The Wiki section (nav label: **Wiki**) has the classic three-panel layout: a page tree
on the left, the knowledge graph in the center, and a metadata inspector on the right.
Left and right panels can be collapsed by clicking the chevron button on their inner
edge; click the chevron again to expand.

![3-panel with selected node](screens/shell-3panel-selected.png)

**Left panel — page tree.** Wiki pages grouped by type (concept, entity, source,
synthesis, comparison). Click any row to select that page and load its metadata in
the right panel. Click the `‹` chevron on the right edge of the left panel to collapse
it and reclaim screen space.

**Center panel — graph.** The same sigma viewer as the Graph section, embedded here
for context. Node size reflects the number of structural connections a page has.
Colors identify page types (CVD-safe palette; legend shown bottom-left).

**Right panel — inspector.** Shows the selected page's frontmatter (title, type,
sources), its relationships (pages it links to and pages that link back to it), and
a read-only content preview. Click the `›` chevron on the left edge of the right panel
to collapse it.

---

### Graph section

The full-bleed knowledge graph. This section shows only the graph canvas — no tree or
inspector. Use the Wiki section if you want the graph alongside the page tree.

![Graph section — sigma viewer with labeled nav rail](screens/navrail-graph-active.png)

- **Node size** scales with the number of structural connections (direct wikilinks and
  shared-source provenance). Larger nodes are more connected.
- **Node color** encodes page type. The legend is always visible in the bottom-left
  corner.
- **Hover** lights up the hovered node and its immediate neighbors. Everything else
  fades to a low opacity so the local neighborhood is easy to read.
- **Drag** a node to reposition it. The new position persists across graph recomputes;
  a dragged node keeps its location even when new pages are ingested.
- **Click** a node to select it. The selected node title is announced for screen
  readers.

The layout is computed server-side (ForceAtlas2 offline). The browser never runs a
force-directed layout, so the UI stays responsive regardless of graph size.

---

### Sources section

The Sources section (nav label: **Sources**) shows the history of all ingest runs for
the current vault and provides two ways to add documents directly from the browser.

![Ingest activity view](screens/ingest-section.png)

#### Uploading a document

The top of the Sources section contains a drag-and-drop upload zone.

- **Drag** a Markdown or plain-text file (`.md`, `.txt`, `.markdown`) onto the zone, or
  click **Browse** to open a file picker.
- Synapse saves the file to `vault/raw/sources/` and the watcher ingests it
  asynchronously. A new run row appears in the list within about 15–30 seconds.
- **Accepted formats in v0.4:** Markdown and plain text only. Uploading a PDF, DOCX, or
  other binary format returns a clear error explaining that multi-format ingest (F12) is
  coming in M5.
- **Size limit:** 25 MB per file (configurable by the operator via `MAX_UPLOAD_BYTES`).
  Larger files are rejected with a message before any data is saved.
- If you upload a file whose name already exists in `vault/raw/sources/`, the existing
  file is replaced and re-ingested (correct incremental behaviour — only the changed
  content is re-processed).

#### Run history

Each row in the list below the upload zone displays:

- **Status badge** — Running (pulsing), Completed, Failed, or Did not converge.
- **Provider** — which inference backend handled the run (Local Ollama, API, or CLI).
- **Pages created** — how many wiki pages this run produced.
- **Cost** — total provider cost in USD to four decimal places (e.g. `$0.0512`). Local
  Ollama runs always show `$0.0000`.
- **Relative time** — "3 hours ago", "yesterday", etc.

Click a row to expand its details in the right panel (error message if failed, full
cost breakdown).

**Run Ingest button** — triggers a new ingest run against the current vault using the
active provider. On success a toast confirms the run started and the list refreshes.
The list polls automatically while any run has status Running.

Note: the review queue for approving or rejecting AI-generated pages is coming in the
next sprint (M5).

---

### Chat section

The Chat section is a multi-conversation interface backed by the configured inference
provider.

![Chat streaming](screens/chat-streaming.png)

**Left panel — conversation list.** All your past conversations for the current vault.
Create a new one with the `+` button. Delete one with the `x` on hover. Conversations
persist across page reloads.

**Center panel — message thread.** Each user message appears in teal; assistant
replies in green. Responses stream token by token as they arrive — you see the reply
build in real time. A **Stop** button interrupts a stream in progress.

When the response is complete, two buttons appear under the assistant message:
- **Regenerate** — re-sends your last message and replaces the previous reply.
- **Save to wiki** — disabled in v0.4; becomes active in M5 when the full retrieval
  pipeline ships.

**Reasoning (`<think>`) blocks.** If the model produces a `<think>…</think>` section
(for example when using a reasoning-capable model), it is shown in a collapsible
"Reasoning" section, collapsed by default. Click it to expand.

**GFM and LaTeX.** Assistant responses are rendered as GitHub-flavored Markdown
(tables, task lists, strikethrough). LaTeX expressions (`\alpha`, `\sum`, etc.) are
converted to Unicode at the end of the stream, not per token. Complex display math
that cannot be converted is left as a fenced code block.

![Chat with completed response](screens/chat-conversation.png)

---

### Settings section

The Settings section uses a two-column layout: a left sub-navigation list of nine
sections and a right content pane that shows the selected section. Click any sub-nav
item to switch the pane without a page reload.

![Settings — General section](screens/settings-section.png)

The nine sections are:

| Section | Contents |
|---------|----------|
| **General** | Context window size and token-budget bar chart |
| **LLM Models** | Add, view, and delete inference provider configurations |
| **Embeddings** | Vector embeddings configuration (coming in M5) |
| **Source Watch** | Automatic folder import (scheduled scan) |
| **API + MCP** | MCP server configuration (coming in M5) |
| **Output** | Conversation history length; language toggle |
| **Interface** | UI preferences (coming in M5) |
| **Maintenance** | Reset settings |
| **About** | Version and build information |

#### General

Choose how many tokens Synapse sends to the model per request: 4K, 8K, 16K, 32K
(default), 64K, 128K, 256K, 512K, or 1M. The token budget is split 60 % conversation
history / 20 % retrieved context / 5 % system prompt / 15 % generation headroom. The
bar chart visualizes absolute token counts for the chosen window size.

#### LLM Models

The LLM Models section lists all configured inference providers. Each row shows the
provider type (Local Ollama, API, or CLI), the model ID, and the scope (Global or
Per-operation). Use this section to manage providers without editing the database.

![Settings — LLM Models with provider list](screens/settings-llm-models.png)

**Viewing providers.** The list is loaded from the backend on every visit. The
currently active provider is shown in the header.

**Adding a provider.** Click **+ Add provider** to expand the add form. Choose the
provider type, enter a model ID (required), optionally enter a base URL (for
OpenAI-compatible endpoints), and select a scope. The **Add** button is disabled until
you enter a model ID. On success, the new row appears in the list immediately.

**Deleting a provider.** Click **Delete** on any row. A confirmation prompt appears
before the deletion is sent. If you are about to delete the last remaining provider, a
warning is shown explaining that ingest and chat will fail without a provider — the
deletion is still allowed, because a misconfigured sole provider should always be
replaceable.

#### Output

**Conversation history length.** Choose how many past messages are sent to the model
with each new chat message: 2, 4, 6, 8, 10, or 20. A smaller history reduces token
cost; a larger history gives the model more context. The setting is persisted in
browser local storage.

**Language.** Toggle between English and Italian. The UI switches immediately; no
reload needed.

#### Source Watch (automatic import)

The Source Watch section (previously called "Automatic import") lets Synapse
periodically scan a mounted folder inside the backend container and import any new or
changed documents automatically — no manual drag-and-drop required.

**How to set it up:**

1. The backend can only see folders that have been mounted into its container. Add a
   bind-mount to `docker-compose.yml` (see [DEPLOY.md §8](DEPLOY.md)) and restart the
   stack. Example: `./import:/import:ro` makes the host folder `./import` visible inside
   the container as `/import`.
2. In Settings > **Source Watch**, enable the toggle.
3. Enter the **container path** (e.g. `/import`). This is the path inside the container,
   not your host machine's path. If the path is not accessible inside the container, a
   warning appears — add the mount and it resolves on the next scan.
4. Choose a **frequency**: every 15 minutes, every hour, every 6 hours, or daily.
5. Click **Save** (or the card auto-saves on change). The scheduler picks up the new
   settings on its next tick without a restart.

The **Run now** button triggers an immediate scan outside the normal schedule. Use it
to test your setup or import a batch without waiting for the next scheduled tick.

After each scan the card shows "Last scan: N minutes ago — M imported". The number is
how many files were copied into `vault/raw/sources/` (new or changed content only —
identical files are skipped). Actual ingest runs for those files appear in the Sources
section with their normal status and cost.

**Important constraints:**
- Only Markdown and plain-text files (`.md`, `.txt`, `.markdown`) are imported in v0.4.
  Other file types in the scanned folder are silently skipped; they will be supported
  when multi-format ingest (F12) ships in M5.
- The scan is non-recursive: only files directly inside the configured folder are
  imported, not files in sub-folders.
- Each scan copies at most 200 files and runs for at most 60 seconds (both limits are
  configurable by the operator). Remaining files are picked up on the next tick.
- A scan that is already in progress will not overlap with a new tick or a "Run now"
  request.

---

### Provider selector

The header shows the currently active provider. Click the provider name to open the
dropdown.

![Provider selector open](screens/provider-selector-open.png)

Three provider types are available:

| Type | Backend | Cost | Best for |
|------|---------|------|---------|
| **Local** | Ollama on the RTX 3060 | Free | Privacy-sensitive vaults; offline use |
| **API** | Anthropic API or OpenAI-compatible endpoint | Pay-per-token | Quality; default recommendation |
| **CLI** | claude-agent-sdk | Pay-per-token | Maximum quality; full agentic ingest loop |

Select a provider and choose the scope: **Global** (applies to all operations) or
**Vault** (overrides for the current vault only). The change takes effect immediately
for the next chat message or ingest run; no page reload needed.

---

## Ingesting your first document

There are three ways to get a document into Synapse:

**Option 1 — Drag and drop in the browser.** Open the Sources section and drop a
`.md` or `.txt` file onto the upload zone (or click Browse). The watcher ingests it
asynchronously; a new run row appears within about 15–30 seconds.

**Option 2 — Place the file directly.** Copy or move a file into `vault/raw/sources/`
on the host. The file watcher detects it and ingests it automatically. You can also
trigger a run manually with the **Run Ingest** button in the Sources section.

**Option 3 — Scheduled folder import.** Configure Settings > Source Watch to scan a
mounted folder on a regular schedule. Any new or changed documents are imported
automatically without manual action (see the Source Watch sub-section above).

Supported formats in v0.4: plain text and Markdown only. PDF, DOCX, images, and
audio/video are coming in M5 (F12).

After ingest, the Sources section shows a Running row that changes to Completed once
the AI has finished generating wiki pages. Switch to the Graph or Wiki section to see
the new nodes appear.

---

## Opening the wiki in Obsidian

The `vault/wiki/` folder is a valid Obsidian vault. Open it directly in Obsidian:

1. In Obsidian, choose **Open folder as vault** and point it at `vault/wiki/`.
2. Obsidian reads the `[[wikilinks]]` and YAML frontmatter that Synapse generates.
3. Synapse auto-generates `vault/wiki/.obsidian/app.json` on startup so the vault
   opens in reading (non-legacy-editor) mode by default.

You can browse, annotate, and link pages in Obsidian. Synapse will not overwrite your
manual edits, though it will regenerate a page if you ingest the same source again.

---

## Status bar

The status bar at the bottom of every section shows:

- **Vault name** (e.g. `default`)
- **Data version** (e.g. `v16`) — increments each time a page is created or updated
- **Uptime** — how long the backend has been running
- **Active provider** (also shown in the header)

---

## What is coming in M5 and M6

The following features are planned for the next sprints and are NOT present in v0.4:

| Feature | Sprint |
|---------|--------|
| 4-phase RAG retrieval with `[n]` inline citations in chat | M5 |
| Save-to-wiki from chat (button present but disabled) | M5 |
| Async HITL review queue (approve / skip / deep-research AI-generated pages) | M5 |
| Deep Research loop (web search via SearXNG, auto-ingest) | M5 |
| Multi-format ingest: PDF, DOCX, PPTX, XLSX, images, audio/video | M5 |
| Cascade deletion (delete a source and clean up all derived pages) | M5 |
| Search, Lint, and Review nav rail items (functional logic ships in M5) | M5 |
| Vector embeddings configuration UI | M5 |
| MCP server configuration UI | M5 |
| Chrome MV3 web clipper | M6 |
| PWA and Tauri v2 desktop packaging | M6 |
| Lint-fix loop | M6 |
| MkDocs documentation site | M6 |
