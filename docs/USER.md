# Synapse User Guide

<!-- Generated: v0.6 sprint 6 | 2026-07-03 -->

> Version: v0.6 (M6 — "Shippable")
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

The rail contains items from top to bottom:

| Label | Section |
|-------|---------|
| **Chat** | Multi-conversation streaming chat (default on first load) |
| **Wiki** | File tree + knowledge graph + page inspector |
| **Sources** | Ingest activity history, upload zone, Run Ingest button |
| **Graph** | Full-bleed sigma knowledge graph |
| **Review** | HITL proposal queue — act on AI-proposed follow-up work |
| **Deep Research** | Web-search loop via SearXNG — synthesize and auto-ingest |
| **Lint** | Bounded wiki health check — flag and fix structural issues |
| **Settings** (pinned at bottom) | LLM providers, context window, language, maintenance |

The nav rail also carries **Review**, **Deep Research**, and **Lint** sections (Review and Deep Research shipped in v0.5; Lint shipped in v0.6).

The vault name, data version, and active provider appear in the status bar at the
bottom of every section.

---

### Wiki section

The Wiki section (nav label: **Wiki**) has the classic three-panel layout: a page tree
on the left, a note reader/editor in the center, and a metadata inspector on the right.
Left and right panels can be collapsed by clicking the chevron button on their inner
edge; click the chevron again to expand.

![3-panel with selected node](screens/shell-3panel-selected.png)

**Left panel — page tree.** Wiki pages grouped by type (concept, entity, source,
synthesis, comparison). Click any row to select that page: its content loads in the
center panel and its metadata loads in the right panel. Click the `‹` chevron on the
right edge of the left panel to collapse it and reclaim screen space.

**Center panel — note reader/editor (NoteView).** Shows the raw markdown of the
selected wiki page (including YAML frontmatter) in a read-only view by default.
Click **Edit** (top-right of the panel) to switch to the CodeMirror 6 editor.
When you are done editing, click **Save** to write the changes back. The backend
applies an optimistic-lock check: if another process (or you in a second tab) changed
the file since you opened it, you will see a "content changed on disk — please reload"
message and the save will be rejected to prevent data loss. Reload the page to get the
latest content and try again.

Saving re-indexes the page inline (links, embeddings, graph) without rescanning the
vault (I1). One graph version bump fires the debounced graph recompute so the Graph
section updates automatically within a few seconds.

> Note: the full-bleed knowledge graph used to appear in the center panel of the Wiki
> section. As of v0.5, the graph lives exclusively in the dedicated **Graph** section
> (nav label: **Graph**). The Wiki section center panel is now the note editor.

**Right panel — inspector.** Shows the selected page's frontmatter (title, type,
sources) and its relationships (pages it links to and pages that link back to it).
Click the `›` chevron on the left edge of the right panel to collapse it.

---

### Reading and editing wiki notes

Synapse lets you read and edit any `wiki/` page directly in the browser using the
CodeMirror 6 editor, without leaving the app.

**To read a note:**

1. Navigate to the **Wiki** section using the nav rail.
2. Click any page in the left-panel tree. The page's raw markdown (including YAML
   frontmatter and `[[wikilinks]]`) appears in the center panel.

**To edit a note:**

1. Select a page in the tree (center panel shows its content).
2. Click **Edit** (top-right corner of the center panel). The panel switches to
   CodeMirror 6 editor mode.
3. Make your changes. The editor supports syntax highlighting for Markdown and YAML.
4. Click **Save** when you are done.

**What happens on Save:**

- The backend writes the file atomically (temp file + rename — no partial writes).
- YAML frontmatter is validated before writing: if the frontmatter is malformed, the
  save is rejected with a 422 error and the file on disk is NOT changed.
- The backend re-indexes the page inline: wikilinks are re-parsed, embeddings updated,
  and the graph version is bumped — all for this single page, with no full rescan (I1).
- The Graph section updates automatically (debounced, within about 5 seconds).

**If the note changed on disk while you were editing:**

You will see a "reload required" message (HTTP 409). This means someone else (or a
background ingest run) updated the same file since you opened it. Click **Reload** in
the center panel to fetch the latest content. Your unsaved edits will be lost — copy
them to a clipboard before reloading if needed.

**Important constraints:**

- You can only edit pages inside `vault/wiki/`. Raw source files in `vault/raw/sources/`
  are read-only in the editor (the backend returns 403 for those paths — K1 vault layer
  separation).
- The maximum editable page size is 4 MB (`MAX_PAGE_CONTENT_BYTES`). Files larger than
  this are displayed read-only with a size warning.
- The graph is not in the Wiki section center panel. Use the **Graph** section (nav rail)
  to view the full-bleed sigma knowledge graph.

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
- **Accepted formats in v0.5:** Markdown, plain text (`.md`, `.txt`, `.markdown`), PDF,
  DOCX, PPTX, and XLSX. Binary files are automatically converted to a companion
  `.extracted.md` file for ingest (F12, ADR-0025); the original binary is stored in
  `vault/raw/sources/`. Images and audio/video are not yet supported (planned for M6).
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

After each ingest run the backend runs a proposal stage and emits review items for
genuinely useful follow-up work (missing pages, research gaps, contradictions). Visit
the **Review** section in the nav rail to act on them.

---

### Chat section

The Chat section is a multi-conversation interface backed by the configured inference
provider.

![Chat streaming](screens/chat-streaming.png)

**Left panel — conversation list.** All your past conversations for the current vault.
Create a new one with the `+` button (or press **Cmd/Ctrl+N**). Delete one with the `x`
on hover. Conversations persist across page reloads.

**Starting a conversation — example prompts.** When a conversation has no messages yet,
the center panel shows the Synapse logo and three clickable example-question chips. Click
any chip to send that question immediately, exactly as if you had typed it and pressed
Enter. The chips are a quick way to explore your vault without having to think of a first
question; you can still type your own message at any time.

**Center panel — message thread.** Each user message appears in teal; assistant
replies in green. Responses stream token by token as they arrive — you see the reply
build in real time. A **Stop** button interrupts a stream in progress.

When the response is complete, two buttons appear under the assistant message:
- **Regenerate** — re-sends your last message and replaces the previous reply.
- **Save to wiki** — active in v0.5; creates a new wiki page from the conversation
  turn via `POST /ingest/from-text` (F6, ADR-0019 §2.7).

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
| **Embeddings** | Vector embeddings on/off toggle; enabled/disabled state; lexical-only notice when off |
| **Source Watch** | Automatic folder import (scheduled scan) |
| **API + MCP** | MCP server connection details; tool list; Claude Desktop snippet |
| **Output** | Conversation history length; language toggle |
| **Interface** | Theme (Light / Dark / System), and other UI preferences |
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
- As of v0.5, Markdown, plain text, PDF, DOCX, PPTX, and XLSX files are imported (F12).
  Binary files are converted to a companion `.extracted.md` before ingest. Images and
  audio/video files in the scanned folder are silently skipped (planned for M6).
- The scan is non-recursive: only files directly inside the configured folder are
  imported, not files in sub-folders.
- Each scan copies at most 200 files and runs for at most 60 seconds (both limits are
  configurable by the operator). Remaining files are picked up on the next tick.
- A scan that is already in progress will not overlap with a new tick or a "Run now"
  request.

#### Interface {#settings-interface}

The Interface section controls the visual theme of the app.

**Theme.** Three options are available from a selector in Settings > Interface:

| Option | Behaviour |
|--------|-----------|
| **Light** | Always uses the light palette, regardless of OS setting. |
| **Dark** | Always uses the dark palette, regardless of OS setting. |
| **System** | Follows the OS appearance setting. If you switch your OS between light and dark mode, the app updates live — no page reload needed. |

The selected theme is persisted in your browser's local storage. CodeMirror (the note editor) automatically switches between its default light theme and the One Dark theme to match. The knowledge graph's canvas background and label colors also follow the resolved theme.

---

### Command palette {#command-palette}

The command palette gives keyboard-first access to every section and every wiki page in one step.

**Opening and closing:**

- Press **Cmd+K** (macOS) or **Ctrl+K** (Windows / Linux) to toggle the palette open or closed.
- Press **Esc** to close without navigating.
- The shortcut works even when the note editor has focus.

**Navigating results:**

1. Start typing to filter by title. The palette searches app sections and all wiki page titles simultaneously, returning up to 20 results.
2. Press **↑** / **↓** to move between results.
3. Press **Enter** to open the selected item. Sections switch the active nav-rail item; wiki pages open in the Wiki section note reader.

**Keyboard shortcuts (no palette needed):**

| Shortcut | Action |
|----------|--------|
| **Cmd/Ctrl+K** | Open / close command palette |
| **Cmd/Ctrl+N** | New conversation (Chat section) |
| **Cmd/Ctrl+1** | Switch to section 1 (Chat) |
| **Cmd/Ctrl+2** | Switch to section 2 (Wiki) |
| **Cmd/Ctrl+3** | Switch to section 3 (Sources) |
| **Cmd/Ctrl+4** | Switch to section 4 (Graph) |
| **Cmd/Ctrl+5** | Switch to section 5 (Review) |

The section-switch and new-conversation shortcuts are ignored while you are typing in a text input or the note editor (to avoid conflicts), except for Cmd/Ctrl+K which remains reachable from any context.

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

Supported formats in v0.5: Markdown, plain text, PDF, DOCX, PPTX, and XLSX (F12).
Images and audio/video are planned for M6.

After ingest, the Sources section shows a Running row that changes to Completed once
the AI has finished generating wiki pages. Switch to the Graph or Wiki section to see
the new nodes appear.

---

### Review section

The Review section (nav label: **Review**) shows the HITL (human-in-the-loop)
proposal queue that Synapse builds after each ingest run. This is where the AI
proposes follow-up work and you decide what to act on (K8: the LLM proposes,
you curate).

![Review queue with proposal cards](screens/review-queue-proposal-cards.png)

#### How proposals are generated

After each orchestrated ingest run, Synapse runs a single bounded proposal call
(fire-and-forget — the pages are already written and this step never blocks or fails
ingest). An anti-spam gate suppresses the call on trivial runs: the call only fires
when the run wrote substantial content (at least 10 000 characters or at least 4 pages)
or when concrete signals exist (dangling wikilinks, analysis-proposed pages that were
not written). Rule-based proposals (missing pages, duplicates) are emitted without any
LLM call; the single LLM call is reserved for the harder suggestion / contradiction /
confirm judgments.

Each proposal becomes one card in the Review section. The list is paginated and
virtualized for large queues.

#### Five proposal types

| Type | What it means | Typical action |
|------|---------------|---------------|
| `missing-page` | A referenced entity or concept has no wiki page yet. Synapse suggests creating one. | **Create** to generate the page on demand, or **Skip** if the entity is not worth a page. |
| `suggestion` | A research gap or follow-up the AI thinks would strengthen the vault. | **Deep Research** to run a web-search loop, **Skip**, or act manually in Obsidian. |
| `contradiction` | The AI detected a conflict between the new content and an existing wiki page. | **Create** a resolution page, **Deep Research** for more context, or **Skip** to ignore. |
| `duplicate` | The proposed title may collide with an existing page (possible merge candidate). | Review the existing page; **Skip** if they cover distinct topics. |
| `confirm` | The AI wants explicit human confirmation before acting on a finding. | **Create** if confirmed, otherwise **Skip**. The sweep never auto-resolves `confirm` items. |

#### The three actions

Each proposal card offers exactly three buttons (no other actions are available):

- **Create** — generates the proposed wiki page on demand. Clicking Create runs the
  bounded orchestrated loop targeting that single page (same AI logic as a normal
  ingest run). The page is written through the same incremental write seam as all other
  pages: one `data_version` bump, one entry in `log.md`, one node in the graph. A
  spinner is shown while generation runs (this takes a few seconds and uses the active
  provider, with cost logged in the Sources run history). On completion the proposal
  card moves to "created" status and the graph refreshes automatically. If generation
  fails, the item remains `pending` so you can retry or skip.

  **Note:** Create replaces the old "Approve" verb from the previous review model. In
  the earlier design, "Approve" was a no-op (the page had already been created). Now
  the page is generated only when you click Create — the AI proposes, you curate (K8).

- **Deep Research** — delegates to the Deep Research loop (F10): Synapse runs a
  multi-query SearXNG web-search cycle, synthesizes the findings, and auto-ingests
  the synthesis as a new wiki page. The proposal topic is derived from the card's
  `proposed_title` or `rationale`. The item moves to `deep_researched` status and a
  link to the research run appears in the Sources section.

- **Skip** — closes the proposal without any action. The item moves to `skipped`
  status and disappears from the pending queue. Skipping is reversible only by
  re-ingesting the source.

#### Auto-resolution sweep

After each ingest run (and after each Create action) Synapse runs an auto-resolution
sweep to close proposals that are no longer relevant:

- **Rule-based pass (no AI cost):** if a `missing-page` or `duplicate` proposal's
  `proposed_title` now matches an existing wiki page title, the item is automatically
  closed (`auto_resolved`). As your wiki grows, these proposals resolve themselves.

- **Conservative LLM pass (optional, bounded):** a single batched call (capped at 8
  items, off-by-default for zero-cost operation — see `REVIEW_SWEEP_LLM_ENABLED`) may
  resolve `suggestion` or `contradiction` items where the LLM judges the concern no
  longer applies. The prompt biases toward keeping items pending on any uncertainty.
  `confirm` items are **never** auto-resolved — they always require human action.

You can also trigger the sweep manually with **POST /review/queue/sweep** (useful after
bulk edits in Obsidian).

> **Note — CLI provider and the review queue (ADR-0025 §7):** when the active provider is
> **CLI** (`CliAgentProvider`), the entire ingest is delegated to the claude-agent-sdk agent
> loop, which writes pages autonomously using in-process MCP tools. In this delegated path,
> the post-ingest proposal stage does **not** run, so no review items are enqueued after a
> CLI-provider ingest. This is a conscious design gap: the review queue is populated only by
> the orchestrated ingest loop (API and Local providers). If you rely on the review queue for
> follow-up curation, use the API or Local provider for ingest and reserve the CLI provider
> for tasks where you want fully autonomous page creation.

#### Screenshots

![Review queue ADR-0034 proposal cards](screens/review-queue-adr0034.png)

---

### Lint section

The Lint section (nav label: **Lint**) runs a bounded health check of the wiki (K2, ADR-0037). Unlike ingest, lint never modifies pages autonomously: every finding requires an explicit human action before any change is applied.

**Running a lint scan:**

1. Navigate to **Lint** in the nav rail.
2. Click **Run Lint**. The backend starts a new scan (bounded by `LINT_MAX_ITER` iterations and `LINT_TOKEN_BUDGET` tokens). A spinner appears while the scan runs.
3. When the scan completes, findings are grouped by category in the panel below. The cost line shows the provider cost for any semantic calls made (Local Ollama runs show `$0.0000`).

**Finding categories:**

| Category | What it flags | Action available |
|----------|--------------|-----------------|
| `orphan-page` | A wiki page with no incoming wikilinks (structurally isolated) | Acknowledge only — flag-only per ADR-0037; no automatic edit |
| `contradiction` | The AI detected conflicting claims between two wiki pages | Acknowledge only — flag-only; resolve manually in the editor or via Deep Research |
| `stale-claim` | A claim in a wiki page may be outdated based on newer ingested content | Acknowledge only — flag-only; review and update manually |
| `missing-xref` | A wiki page mentions a concept that has a dedicated page but no `[[wikilink]]` | **Apply** to insert the missing wikilink, or Dismiss |

**Apply vs Acknowledge:**

- **Apply** — available only for `missing-xref` findings. Clicking Apply writes the suggested `[[wikilink]]` into the body of the referencing page (targeted edit, no vault rescan — I1). The edit is written through the same incremental seam as all other page writes: one `data_version` bump, one entry in `log.md`, graph refresh within about 5 seconds.
- **Acknowledge** — used for the three flag-only categories (`orphan-page`, `contradiction`, `stale-claim`). These categories surface observations for human judgment but the Lint section will never edit human-curated content on their behalf (K8). Acknowledging moves the finding to `acknowledged` status and removes it from the active list.
- **Dismiss** — discards a finding without action (works on any category).

The human gate is intentional: scan findings are never auto-applied across a run. You must call **Apply** (or **Acknowledge**) per finding to close it. This keeps the AI in a propose-only role and the human in control of actual content changes (K8).

**Empty state:** when the wiki has no findings, the panel shows "Wiki is healthy — no findings." The Run Lint button remains available to trigger a fresh scan at any time.

---

### Web Clipper section

The Synapse web clipper is a Chrome MV3 browser extension (F11, ADR-0038). It lets you clip any web page directly into your vault from the browser, without leaving the page you are reading.

**Installing the extension:**

The extension source lives under `extension/` in the repository. To install it:

1. Open Chrome and navigate to `chrome://extensions/`.
2. Enable **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked** and select the `extension/` directory from the repository root.
4. The Synapse icon appears in the browser toolbar.

**Configuring the extension (required before first use):**

1. Click the Synapse icon and select **Options** (or right-click the icon and choose **Extension options**).
2. Set the **Base URL** to your Synapse backend: `http://localhost:8000` for local development, or your Cloudflare Tunnel URL for remote access (e.g. `https://synapse.yourdomain.com`).
3. Set the **Token** to the value of your `CLIP_TOKEN` environment variable (the bearer token you set when deploying the backend).
4. Click **Save**.

The operator must also ensure the backend is configured with `CLIP_ENABLED=true` and `CLIP_TOKEN` set to the same value, and that `CLIP_ALLOWED_ORIGINS` includes your Chrome extension's origin ID (shown in `chrome://extensions/` as the extension's ID, prefixed with `chrome-extension://`).

**Clipping a page:**

1. Navigate to any web page you want to add to your vault.
2. Click the Synapse extension icon in the toolbar.
3. The popup shows the page title, URL, and a Markdown preview (converted from the page's main content via Mozilla Readability + Turndown).
4. Optionally edit the title or select a target vault from the dropdown.
5. Click **Clip**. The extension posts the Markdown to `POST /clip`, which writes it to `vault/raw/sources/` and triggers the normal watcher-based ingest pipeline (I1/K1). No second ingest path is introduced.
6. A confirmation toast appears in the popup. The page appears in the Sources section of the Synapse UI within about 15–30 seconds (same timing as any other ingest).

**Security notes:**

- Every clip request requires the bearer token. Requests without a valid token receive 401.
- The body is capped at 2 MB (configurable via `CLIP_MAX_BODY_BYTES`). Oversized payloads receive 413.
- The backend validates the request Origin against `CLIP_ALLOWED_ORIGINS` and writes only inside `vault/raw/sources/` (path-safe join). The design is the intentional inverse of the reference app's unauthenticated, unvalidated clip server (see ADR-0038 for the security rationale).

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

## What shipped in M5 and M6

The following features shipped in v0.5 (M5):

| Feature | Notes |
|---------|-------|
| 4-phase RAG retrieval with `[n]` inline citations in chat | Phases 1–4: dense seed → graph-expand → budget → assembly |
| Save-to-wiki from chat | Button active; routes through `POST /ingest/from-text` |
| Async HITL review queue — proposal model with lazy on-demand Create (ADR-0034) | Review section; `/review/queue` REST endpoints; five proposal types (missing-page, suggestion, contradiction, duplicate, confirm); Create / Deep Research / Skip actions; auto-resolution sweep |
| Deep Research loop (web search via SearXNG, auto-ingest) | `/research/start` REST; bounded `max_iter` + `max_queries_per_iter` |
| Multi-format ingest: PDF, DOCX, PPTX, XLSX | F12; images/AV are placeholder (M6) |
| Cascade deletion (delete a source and clean up all derived pages) | Mandatory dry-run preview before destructive apply |
| Embeddings on/off toggle + lexical fallback | `EMBEDDINGS_ENABLED=false` starts without Qdrant/bge-m3 |
| Remote MCP over HTTP at `/mcp/server` | Requires `MCP_AUTH_TOKEN`; read-only by default |
| OpenAI-compatible embeddings adapter | `EMBEDDING_FORMAT=openai` for hosted endpoints |
| MCP server configuration panel (Settings > API + MCP) | Displays connection details, tools, and Claude Desktop snippet |

---

## Install as an app (PWA)

Synapse ships as a Progressive Web App (F15 / AC-F15-1). Once the built frontend is
served over HTTPS (or localhost), any modern browser will offer a "Install" / "Add to
Home Screen" prompt.

**How to install:**

1. Open Synapse in Chrome, Edge, or Safari (iOS 16.4+).
2. Look for the install icon in the browser's address bar (Chrome/Edge) or tap
   "Share > Add to Home Screen" (Safari / iOS).
3. Accept the prompt. Synapse opens in standalone window mode with no browser chrome.

**Offline behaviour:**

- The app shell (HTML, JavaScript, CSS, icons) is cached by a Workbox service worker
  and loads instantly even when the backend is unreachable.
- All backend API calls (`/pages`, `/graph`, `/chat`, `/search`, etc.) are
  **always network-first** — they are never served from the service worker cache.
  If the backend is offline, those calls fail fast with a network error (no stale
  data is ever returned). This preserves the `dataVersion` freshness model (I1).

**Notes:**

- The service worker is registered in production builds only. `npm run dev` (Vite HMR)
  does **not** activate it, so development is unaffected.
- To uninstall: use the browser's "Manage apps" / site settings to remove the app.
- The Tauri v2 desktop wrapper (F15) ships in v0.6 and provides the same offline shell
  as a native window without an external browser — see the Desktop app section below.

---

## Desktop app (Tauri v2) {#desktop-app}

The Synapse desktop app is a native Tauri v2 window for macOS and Windows that wraps
the same frontend as the PWA (F15, ADR-0047). It is the right choice when you want a
native OS window pinned to a specific Synapse backend, without keeping a browser tab
open. All wiki, graph, and chat features available in the web UI are available in the
desktop app without any feature differences.

Download the latest installer from the GitHub releases page (look for tags beginning
with `desktop-v`). See [DEPLOY.md §7](DEPLOY.md#desktop-app) for install instructions,
unsigned-binary warnings, and build-from-source steps.

### Connect screen (first launch)

The desktop app does not know the address of your backend at install time. The first
time you open it — and any time you click **Change server** in the header — you see a
full-screen branded **Connect** screen.

On a true first launch the app also probes `http://localhost:8000` in the background:
if a Synapse backend is running on the same machine, the field is prefilled and a
"server detected" hint appears — you only have to click **Connect**. After a
**Change server**, the field is prefilled with the last address you connected to.

**What to enter:** the base URL of your Synapse backend, with a scheme and no trailing
slash. Examples:

| Backend location | URL to enter |
|-----------------|--------------|
| Same machine | `http://localhost:8000` |
| TrueNAS via Tailscale | `http://truenas:8000` |
| Cloudflare Tunnel | `https://synapse.yourdomain.com` |

Click **Connect**. The app sends a `GET /status` probe to the URL. The `/status` probe
checks that the backend is reachable and returns its vault ID and data version — it is
a read-only, side-effect-free check. On a 2xx response, the URL is saved to local
storage (`synapse.serverUrl`) and the full interface loads. If the probe fails or times
out, an error message appears and the Connect screen stays open: the URL is **not** saved
and you can correct it and try again.

The Connect screen is only shown in the desktop app. Opening Synapse in a browser
(PWA or tab) is unaffected — browsers use a relative URL to the same origin and never
see this screen.

### Server chip in the header {#desktop-server-chip}

Once connected, the header shows a small chip with the backend hostname. Click it to
open a dropdown. The dropdown lists up to the last five servers you have successfully
connected to, plus a **Change server** entry at the bottom.

- **Select a recent server** — the app switches to that backend immediately by saving the
  URL and reloading the page. A full reload is intentional: switching backends resets all
  cached queries, conversations, and graph data so no stale cross-server state leaks into
  the new session.
- **Change server** — clears the stored URL and returns to the Connect screen. Use this
  to enter a new address that is not yet in the recent-servers list.

Only servers that passed a successful `GET /status` probe are added to the list, so the
dropdown never contains an address that has never connected.

The server chip is only visible in the desktop app.

### Zoom {#desktop-zoom}

The desktop app supports adjustable UI zoom (Tauri only — not available in browser / PWA):

| Shortcut | Action |
|----------|--------|
| **Cmd/Ctrl +** | Zoom in (up to 140 %) |
| **Cmd/Ctrl −** | Zoom out (down to 80 %) |
| **Cmd/Ctrl 0** | Reset to 100 % |

Zoom adjusts in 10 % steps and is persisted across restarts. If text or controls appear
too small or too large on your display (HiDPI or 4K screens especially), use
**Cmd/Ctrl +** / **Cmd/Ctrl −** to tune it.

### Ingest-completion notifications {#desktop-notifications}

When an ingest run finishes, the desktop app sends a native OS notification — so you can
start an ingest, switch to another app, and be notified when Synapse is done without
polling the Sources section.

The first time an ingest completes after installing the desktop app, the OS asks whether
to allow notifications from Synapse. Grant permission to enable this feature; if you
dismiss the prompt, no notification fires for that run. You can grant or revoke
notification permission at any time via your OS system settings.

Notifications are desktop-only (`isTauri()` guard) and do not appear in the browser /
PWA build.

The following features also shipped in v0.6 (M6):

| Feature | Notes |
|---------|-------|
| Lint-fix loop (K2) | Bounded human-gated wiki health check; six `/lint/*` endpoints; `LINT_*` env vars |
| Chrome MV3 web clipper (F11) | Clips web pages → `vault/raw/sources/` via secure `POST /clip`; `CLIP_*` env vars |
| PWA — manifest + offline service worker (F15) | Install Synapse as an app from any modern browser |
| Tauri v2 desktop shell (F15) | Native desktop app for macOS, Windows, and Linux (binaries on GitHub Releases) |
| CI gate (F15) | Tests, pinned linters, and mmdc Mermaid render check on every `sprint/**` push |
| purpose.md injection verified (F2) | `vault/purpose.md` confirmed injected into ingest and chat provider contexts |
| Dark / Light / System theme (F16, ADR-0048) | Settings > Interface; follows OS live when "System"; CodeMirror + sigma follow the resolved theme |
| Command palette + keyboard shortcuts (F16, ADR-0048) | Cmd/Ctrl+K palette (sections + wiki pages, ↑↓/Enter/Esc); Cmd/Ctrl+N new conversation; Cmd/Ctrl+1..5 section switch |
| Chat empty-state example prompts (F1, ADR-0048) | Three clickable chips on a new conversation; click to send immediately |
| Desktop multi-server dropdown (F15, ADR-0048) | Server chip lists last 5 connected servers; switching reloads the app |
| Desktop zoom (F15, ADR-0048) | Cmd/Ctrl +/−/0 adjusts UI scale 80–140 %; persisted across restarts |
| Desktop ingest notifications (F15, ADR-0048) | Native OS notification on ingest completion; permission requested on first fire |
