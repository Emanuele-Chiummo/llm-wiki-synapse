# Synapse User Guide

<!-- Updated: v2.0.0 docs/2.0.0-veritieri | 2026-07-17 -->

> Version: v2.0.0
> Language toggle: English / Italian available in Settings.
>
> **2.0.0 note:** The JSON ingest pipeline ("json" mode) has been removed. Ingest always uses the
> block-based engine. API error responses now use the stable envelope `{"error": {"code": "...",
> "message": "...", "status": N, "details": null}}` — if you have scripts that parsed `{"detail":
> "..."}`, update them to read `error.message` instead.

---

## 1. What is Synapse?

Synapse is a self-hosted web service that turns a folder of raw documents into a
self-organizing knowledge wiki. Drop a file into `vault/raw/sources/`, and Synapse
analyzes it with a configurable AI provider, creates structured wiki pages in
`vault/wiki/`, links them to related concepts, and lays out the whole knowledge graph
for you to explore.

The design follows the **Karpathy LLM Wiki pattern**: the AI maintains the wiki, and
you curate it. The human curates; the LLM maintains. At no point does the AI apply a
change to your wiki without either your explicit approval (lint, review queue) or
the triggering of an explicit ingest action you initiated.

### The vault model

Synapse manages three layers inside a single `vault/` directory:

| Layer | Location | Who writes it |
|-------|----------|---------------|
| **Raw sources** | `vault/raw/sources/` | You — drop files here; Synapse never touches the originals |
| **Wiki pages** | `vault/wiki/` | Synapse generates; you can edit directly |
| **Rules and purpose** | `vault/schema.md` + `vault/purpose.md` | You set the goal and rules; Synapse respects them |

The `wiki/` folder is a valid **Obsidian vault**. Generated pages use compact YAML frontmatter
(`type`, `title`, `created`, `updated`, optional `tags`/`related`; corpus pages also carry the
reserved `synapse_generation_key`). Source provenance remains indexed in Postgres. Pages use
`[[wikilinks]]` and an
auto-generated `.obsidian/` configuration. You can open `vault/wiki/` directly in
the Obsidian app without any conversion or export step.

### What Synapse does not do

- It does not modify `vault/raw/sources/` — raw files are immutable.
- It does not re-scan the entire vault on every change (I1: only the affected files
  are re-indexed).
- It does not run a force-directed layout on the browser — graph layout is computed
  server-side and cached (I2).
- It does not re-render on every streaming token — Markdown and LaTeX are parsed at
  the end of a stream (I3).

---

## 2. Quick start — the core journey

1. **Open the app** — you land on the Home dashboard. Glance at the KPI row and
   domain section cards.
2. **Drop a document** into `vault/raw/sources/` (or use the upload zone in
   Sources), or drag a file onto the upload zone in the browser.
3. **Watch the graph grow** — the knowledge graph updates automatically once the
   AI finishes generating wiki pages.
4. **Inspect a page** — click any node in the Graph section, or any row in the Wiki
   tree, to read its content and metadata.
5. **Chat with your wiki** — ask questions in the Chat section; answers stream
   token by token with inline citations you can click.
6. **Act on proposals** — visit the Review section to see what the AI thinks is
   worth following up on; choose Create, Deep Research, or Skip per item.
7. **Configure your provider** — in Settings, open "AI & provider" to add, change,
   or remove an inference provider.

---

## 3. The interface

Synapse uses a dark-themed shell with a labeled navigation rail on the left side.

### Navigation rail

The leftmost strip is approximately 72 px wide. Each item shows an icon and a
persistent text label below it. The active section is highlighted with a rounded
rectangle. The rail never hides labels on desktop — on screens narrower than 768 px
the labels are hidden and only icons are shown.

The rail is split into two tiers:

**Top tier — places:**

| Label | Section | Description |
|-------|---------|-------------|
| **Home** | Home dashboard | Vault KPIs, recent activity, domain section cards |
| **Chat** | Chat | Multi-conversation streaming chat with retrieval |
| **Wiki** | Wiki | Three-panel page browser, editor, and inspector |
| **Sources** | Sources | Raw source file browser + upload zone |
| **Search** | Search | Full-text and semantic search across wiki pages |
| **Graph** | Graph | Full-bleed sigma.js knowledge graph |

**Middle tier — tools:**

| Label | Section | Description |
|-------|---------|-------------|
| **Lint** | Lint | Bounded wiki health check — find and fix structural issues |
| **Review** | Review | HITL proposal queue — act on AI-proposed follow-up work |
| **Deep Research** | Deep Research | Web-search loop via SearXNG — synthesize and auto-ingest |
| **Ingest** | Ingest | Ingest run history and cost ledger |
| **Convert** | Convert | Dedicated Marker PDF conversion surface |

**Bottom (pinned):**

| Label | Section |
|-------|---------|
| **Settings** | All runtime configuration — 5 groups, 18 pages |

The vault name, data version, backend uptime, and active provider are shown in the
status bar at the bottom of every section.

The **command palette** (Cmd/Ctrl+K) lets you jump to any section or any wiki page
by typing its name. Keyboard shortcuts Cmd/Ctrl+1 through +5 switch to the first
five sections; Cmd/Ctrl+N creates a new conversation in Chat.

---

### Home dashboard {#home-dashboard}

The Home section is the default landing screen. It loads once on mount and polls
`GET /status` every 10 seconds to detect vault data-version changes (only re-fetches
stats when `data_version` changes — no wasteful polling).

#### KPI row

Seven cards span the top:

| Card | What it shows |
|------|--------------|
| **Pages** | Total live wiki pages |
| **Links** | Total wikilink edges in the knowledge graph |
| **Communities** | Louvain communities detected (0 if graph not yet computed) |
| **Review** | Open HITL review items — accented when non-zero |
| **Lint** | Open lint findings — accented when non-zero |
| **Monthly AI spend** | Month-to-date provider cost in USD |
| **Data version** | Current `data_version` counter |

#### Recent activity

The ten most recently updated wiki pages, each with a relative timestamp. Clicking
a row switches to the Wiki section.

#### Server update banner

When the frontend version and the backend version (from `GET /status`) differ and
the backend version is not `"dev"`, a dismissible banner appears asking you to
pull the new backend image. It is stored in `sessionStorage` and does not reappear
unless you reload the tab.

#### Domain section cards {#home-sections}

Below recent activity, a responsive grid shows one card per domain in your
vocabulary, followed by an **Unclassified** card. Each card shows:

- Domain name and page count.
- A color-coded SVG mini-bar showing the type breakdown (concept / entity / source /
  synthesis / comparison) proportionally — computed once on mount, no charting library.
- Type counts in text form.
- Last-activity timestamp.
- Up to three most-connected pages in that domain (by graph degree).

Clicking a domain card writes the domain name to `localStorage` as a filter
and navigates you to the Wiki section, which loads with only that domain's pages
visible. Clicking **Unclassified** clears the filter and shows all untagged pages.

When no vocabulary is configured, a "No domains configured" placeholder appears
with a link to Settings where you can define the vocabulary.

---

### Wiki section

The Wiki section (nav label: **Wiki**) has a three-panel layout: a page tree on
the left, a note reader/editor in the center, and a metadata inspector on the right.
Both side panels are collapsible via the chevron buttons on their inner edges.

**Left panel — page tree.** Pages grouped by type (concept, entity, source,
synthesis, comparison). Click a row to select that page. The `+` button in the
panel header creates a new stub wiki page inline (title + type; one `data_version`
bump, no full rescan — I1).

**Center panel — NoteView.** Displays the raw Markdown of the selected page
(including YAML frontmatter and `[[wikilinks]]`). Click **Edit** to switch to the
CodeMirror 6 editor. Click **Save** to write changes back.

Save behavior:
- The backend writes the file atomically (temp file + rename).
- YAML frontmatter is validated before writing; malformed frontmatter is rejected
  with 422 and the disk file is unchanged.
- Re-indexes inline: wikilinks, embeddings, graph — all for this single page, no
  vault rescan (I1).
- Bumps `data_version` and triggers debounced graph recompute (within ~5 seconds).
- An optimistic-lock check prevents you from overwriting changes made by another
  process; if a conflict is detected you receive a 409 and must reload.

**Unsaved-changes guard.** Navigating away from a page with pending edits shows a
"Unsaved changes — continue?" dialog with **Stay** and **Discard changes** buttons.

**Right panel — inspector.** Shows the selected page's frontmatter (title, type,
sources) and its relationships (outgoing wikilinks + pages that link back to it).

> Note: the full-bleed knowledge graph lives in the dedicated **Graph** section (nav
> label: **Graph**), not inside the Wiki section.

---

### Graph section

The full-bleed knowledge graph. This section shows only the graph canvas.

- **Node size** scales with the number of structural connections.
- **Node color** encodes page type. The legend is always visible in the bottom-left corner.
- **Hover** highlights the node and its immediate neighbors; everything else fades.
- **Drag** a node to reposition it. The position persists across graph recomputes.
- **Click** selects a node and announces its title for screen readers.
- The graph is polled every 5 seconds: only re-fetches if `data_version` changed.
  The browser never runs a force-directed layout (I2).

#### Community drill-down

Nodes are grouped into communities by the Louvain algorithm. Click a community area
to open a detail panel showing:

- **Member list** — every page in the community, clickable to open in Wiki.
- **Cohesion score** — 0–1; how tightly connected the community is internally.
  A score below `GRAPH_COHESION_WARN` (default 0.15) is flagged with a warning.

#### Edge signal breakdown

Click any edge to open an edge detail panel showing the four-signal decomposition:

| Signal | Coefficient | What it measures |
|--------|------------|-----------------|
| Direct wikilinks | 3.0 | `[[wikilinks]]` between the two pages |
| Shared sources | 4.0 | Raw source files that both pages derive from |
| Adamic-Adar index | 1.5 | Common-neighbor structural similarity |
| Type affinity | 1.0 | Cross-type bonus or same-type penalty |

---

### Sources section

The Sources section (nav label: **Sources**) is the raw source file browser. Use it
to upload documents and to see all ingest runs for the current vault.

#### Uploading a document

Drag a file onto the upload zone at the top, or click **Browse** to open a file
picker.

Accepted formats (v0.8+): Markdown, plain text (`.md`, `.txt`, `.markdown`), PDF,
DOCX, PPTX, XLSX, images (`.png`, `.jpg`, `.jpeg`, `.webp`), and audio/video
(`.mp3`, `.m4a`, `.wav`, `.mp4`, `.mov`, `.webm`).

Size limit: 25 MB per file (configurable via `MAX_UPLOAD_BYTES`). Uploading a file
whose name already exists in `vault/raw/sources/` replaces and re-ingests it
(incremental — I1).

#### Run history

Each row shows status badge (Running / Completed / Failed / Did not converge),
provider type, pages created, cost in USD, and a relative timestamp. Click a row to
expand full details.

**Run Ingest** triggers a new ingest run against the current vault using the active
provider. After each ingest run, the backend runs a proposal stage and enqueues
review items for genuinely useful follow-up work. Visit **Review** to act on them.

#### Bulk select

Activate **Select** in the section header to enter bulk-select mode. Check rows,
use **Select all**, then **Delete selected** to cascade-delete all selected source
files and their derived wiki pages in one call. A dry-run summary is shown before
the destructive apply.

---

### Search section

The Search section supports filtering and sorting results.

**Type facet.** Filter chips: All, Concept, Entity, Source, Synthesis, Comparison.
Click a chip to restrict results to that type; click **All** to clear.

**Sort.** A selector in the toolbar switches between:

| Option | Behaviour |
|--------|-----------|
| **Relevance** (default) | Ranked by vector similarity or lexical score |
| **Newest first** | By `created_at` descending |
| **Oldest first** | By `created_at` ascending |

Both facet and sort are server-side; changing either re-fetches results immediately.

---

### Chat section

The Chat section is a multi-conversation interface backed by the configured inference
provider.

**Left panel — conversation list.** All past conversations. Create a new one with
`+` or Cmd/Ctrl+N. Delete with the `x` on hover. Conversations persist across page
reloads. New conversations are auto-titled from the first 50 characters of your first
message. Double-click a title to rename it inline (persisted immediately).
A search box at the top filters rows in real time by title.

**Center panel — message thread.** Responses stream token by token. A **Stop**
button interrupts a stream. Once a response is complete:
- **Regenerate** re-sends your last message and replaces the previous reply.
- **Save to wiki** creates a new wiki page from the conversation turn.

**Inline citations.** When the assistant answer contains `[n]` markers they become
clickable superscript links once streaming finishes. Clicking one opens the
referenced wiki page in the Wiki section.

**Example prompts.** When a conversation has no messages, three clickable chips
suggest starter questions. Clicking one sends that question immediately.

**Reasoning blocks.** If the model produces a `<think>…</think>` section it is shown
in a collapsible "Reasoning" block, collapsed by default.

**GFM and LaTeX.** Responses are rendered as GitHub-flavored Markdown. LaTeX
expressions are converted to Unicode at the end of the stream, not per token (I3).

#### Retrieval modes

The chat composer toolbar shows a segmented control labelled **Retrieval mode**:

| Mode | What it does |
|------|-------------|
| **Fast** | Minimal retrieval; lowest latency |
| **Standard** | Full 4-phase retrieval (tokenized → graph-expand → budget → assemble) — default |
| **Deep** | Extended graph expansion and budget; higher quality, slower |
| **Local first** | Prioritizes locally-stored context before vector search |

#### Web search toggle

A **Web** button with an emerald indicator in the composer enables or disables live
web search via SearXNG for the next message. When enabled, the assistant can fetch
current web sources before answering; web sources appear in a "WEB SOURCES" block in
the reply.

#### Image attachments

The paper-clip button attaches up to 4 images (max 5 MB each). Only available when
the active provider reports `supports_vision=true` — the button is disabled with a
tooltip when vision is not supported.

---

### Review section {#review}

The Review section (nav label: **Review**) shows the HITL proposal queue. The AI
proposes follow-up work; you decide what to act on.

#### How proposals are generated

After each ingest run, Synapse runs one bounded proposal pass. This applies to API/Local and CLI
providers. The delegated CLI path supplies the real source text plus bounded excerpts from only
the pages written in that run; it does not scan the vault or invent a Stage-1 analysis. Rule-based
proposals (missing pages, duplicates) are emitted without an LLM call.

Rules and AI have separate capacity: by default up to 8 rule proposals and 12 AI proposals, with
20 total after deduplication. A matching AI proposal wins when it carries the richer rationale or
query context, so missing-link volume cannot starve detailed review suggestions.

Use the filter bar to combine status, item type, proposal origin and proposed page type. Each card
shows its origin (`rule`, `ai`, `corpus`, `system`, `lint` or migrated `legacy`), proposed type and,
after Create, the effective type that was actually written. Query badges distinguish absent,
title-only and contextual search queries before you accept a proposal.

#### Proposal types

| Type | What it means |
|------|--------------|
| `missing-page` | A referenced entity or concept has no wiki page yet |
| `suggestion` | A research gap the AI thinks would strengthen the vault |
| `contradiction` | A conflict between new content and an existing wiki page |
| `duplicate` | The proposed title may collide with an existing page |
| `confirm` | The AI wants explicit human confirmation before acting |
| `purpose-suggestion` | Recent content may have drifted outside `purpose.md` scope |
| `schema-suggestion` | Emerging frontmatter pattern not covered by `schema.md` (default off) |

#### The three actions

- **Create** — generates the proposed wiki page on demand, running the bounded
  orchestrated loop for that single page. A spinner shows while generation runs;
  cost is logged in the Sources run history.
- **Deep Research** — delegates to the Deep Research loop: Synapse runs a
  multi-query SearXNG web-search cycle, synthesizes findings, and auto-ingests the
  synthesis as a new wiki page.
- **Skip** — closes the proposal without action. Reversible only by re-ingesting
  the source.

#### Auto-resolution sweep

After each ingest run and after each Create action, a rule-based sweep closes
`missing-page` and `duplicate` proposals whose `proposed_title` now matches an
existing page. An optional bounded LLM pass (capped at 8 items, default on) may
close `suggestion` or `contradiction` items. `confirm` items are never
auto-resolved.

#### Corpus comparison and synthesis

The Home dashboard offers two explicit corpus actions when at least three eligible pages exist:

- **Generate now** evaluates bounded same-domain clusters and may automatically write
  high-confidence comparison/synthesis pages.
- **Propose only** performs the same bounded deterministic evaluation without requiring an
  inference provider and sends eligible clusters to Review (`origin=corpus`). It never writes a
  corpus page automatically.

Corpus generation is intentionally conservative. Every member must share a real `domain/*` tag;
untagged and mixed-domain candidates are skipped and counted. Run bounded domain backfill first if
the Home diagnostics report many untagged skips. The UI polls only while the operation is active
and then shows written, proposed, duplicate and untagged counts.

Each new corpus page has a stable identity derived from its kind plus sorted canonical member
paths. Re-running normally skips an existing identity before any model call. `force=true` may
regenerate the content but updates the same deterministic file; it does not create a second page.

Operators can inspect legacy duplicates without changing data:

```text
GET /ops/synthesize/audit?max_pages=500
```

The report is dry-run only. Synapse 1.6.0 never deletes, merges, renames or backfills legacy pages
automatically.

---

### Lint section

The Lint section (nav label: **Lint**) runs a bounded health check of the wiki.
Lint never modifies pages autonomously — every fix requires explicit human approval.

**Running a lint scan:** click **Run Lint**. The backend starts a bounded scan
(capped by `LINT_MAX_ITER` iterations and `LINT_TOKEN_BUDGET` tokens). A spinner
appears while the scan runs.

**Finding categories:**

| Category | What it flags | Actions |
|----------|--------------|---------|
| `orphan-page` | A page with no incoming wikilinks | Acknowledge |
| `contradiction` | Conflicting claims between two pages | Acknowledge |
| `stale-claim` | A claim that may be outdated by newer content | Acknowledge |
| `missing-xref` | Mentions a concept that has a page but no `[[wikilink]]` | **Apply** or Dismiss |

**Apply** (only for `missing-xref`) inserts the missing wikilink into the page
body — a targeted edit, one `data_version` bump, no vault rescan (I1).
**Acknowledge** closes a flag-only finding without editing anything.
**Dismiss** discards without action.

**Scheduled lint.** In Settings, under the **Automation** page (AI behaviour group),
you can schedule lint scans to run automatically at Off / Hourly / Daily / Weekly
frequency. A **Run now** button triggers an immediate scan. Only the scan is
scheduled — applying fixes is always a manual, human-approved action.

---

### Deep Research section

The Deep Research section (nav label: **Deep Research**) runs a bounded
multi-query SearXNG web-search loop and synthesizes the results into a new wiki page.

**How to use:**

1. Navigate to **Deep Research** in the nav rail.
2. Enter a research topic in the text field.
3. Click **Start Research**.

Synapse runs multiple SearXNG search queries against the topic, fetches the top
results (with an SSRF guard blocking private-network addresses), assesses source
quality, refines queries, and synthesizes a wiki page from the best sources. The
run is bounded by `max_iter` and `token_budget` (I7). The synthesis is
auto-ingested into `vault/wiki/` when the run completes.

All research runs are listed below the input field. Click a run to see its detail:
sources fetched, cost, and the synthesis text.

**Web search must be configured.** The backend must have `SEARXNG_URL` set in the
environment, and the SearXNG instance must be reachable. Configure the URL in
Settings → AI behaviour → **Web search**.

---

### Ingest section

The Ingest section (nav label: **Ingest**) is the run-history and cost ledger. Every
ingest run (triggered by the watcher, an upload, a manual trigger, or a Review Create
action) appears here. Each row shows provider, pages created, cost, and timing. v1.6.0 also exposes
an optional per-type count (`entity`, `concept`, `source`, `query`, `synthesis`, `comparison`) in
the API so generation differences between providers and runs can be diagnosed precisely.

---

### Convert section

The Convert section (nav label: **Convert**) is the dedicated interface for the
Marker PDF conversion microservice. See [Higher-quality PDF extraction with Marker](#marker-pdf)
for full details.

---

## 4. Inference providers (F17) {#providers}

All AI operations — ingest, chat, lint, deep research, review proposals — run through
the `InferenceProvider` abstraction. You choose which backend to use; no AI model is
hardcoded anywhere.

### The three provider types

| Type | Backend | Ingest strategy | Best for |
|------|---------|-----------------|---------|
| **Local** | Ollama on your GPU via Ollama's `/api/chat` | Orchestrated loop (analyze → generate → validate → retry, max N) | Privacy-sensitive vaults; offline use; zero cost |
| **API** | Anthropic Messages API or any OpenAI-compatible endpoint | Orchestrated loop with native tool-calling when available | Quality + cost control; recommended default |
| **CLI** | `claude-agent-sdk` bundled CLI | Delegated — the agent runs its own autonomous loop | Maximum quality; full agentic ingest |

**Capability-aware routing** is done by the backend, not you: when the active
provider has `supports_agentic_loop = True` (CLI only), the orchestrator delegates
the full ingest to the agent. Otherwise it runs the step-by-step orchestrated loop.

**Cost tracking.** Every AI call logs `total_cost_usd` to the database. Local Ollama
runs always log `$0.0000`. View the month-to-date total in Settings → System →
**Costs**.

### Adding a provider

1. Open **Settings** (bottom of the nav rail).
2. Select **AI & provider** under the Essentials group.
3. Click **+ Add provider**.
4. Choose the **Type** (Local / API / CLI), enter a **Model ID**, optionally enter
   a **Base URL** (required for non-default Ollama or OpenAI-compatible endpoints),
   and choose a **Scope** (Global or Vault).
5. Click **Add**. The new row appears immediately.

Provider configuration precedence (most specific wins):
`operation + vault_id` > `vault` > `global`

> **API keys are NOT stored in the UI.** Set `ANTHROPIC_API_KEY` or your
> OpenAI-compatible key as an environment variable before starting the container.
> See `DEPLOY.md §4` for details.

### Selecting the active provider

The header shows the currently active provider name. Click it to open the provider
dropdown and switch between configured providers. The change takes effect for the
next chat message or ingest run without a page reload.

### CLI subscription auth {#cli-auth}

When the active provider is **CLI** (`CliAgentProvider`), Synapse calls the
`claude-agent-sdk` bundled Claude CLI. The CLI can authenticate either via
`ANTHROPIC_API_KEY` (pay-per-token) or via a **Claude subscription OAuth token**
(included with your Claude Pro/Max subscription — effectively $0 per token).

**To use your subscription (no API key needed):**

1. On the host machine where Synapse runs, open a terminal and run:
   ```
   claude setup-token
   ```
   The command prints a token starting with `sk-ant-oat01-…`.
2. Copy the full token.
3. In Synapse, open **Settings → Essentials → AI & provider**.
4. Scroll to the **CLI Subscription Auth** sub-section.
5. Paste the token into the **Subscription OAuth token** field.
6. Click **Save**.

Synapse stores the token in the application database and injects it into the Claude
CLI environment at runtime, scrubbing `ANTHROPIC_API_KEY` so the subscription is
used instead. The token source badge on the page shows `db` when a database-stored
token is active.

> **Security note:** the token is stored in the application database in plaintext.
> Treat database backups accordingly. To rotate: re-run `claude setup-token` on the
> host, then paste and save the new token in Settings. To revoke: click **Clear** on
> the CLI Auth section and the CLI will fall back to env / unconfigured.

**v1.3.10 — live token streaming for CLI.** Before v1.3.10, the CLI provider
delivered the entire chat response as a single event. As of v1.3.10, the backend
enables `include_partial_messages` on the claude-agent-sdk, so chat responses in
CLI mode stream token by token just like the API and Local providers. If the SDK
version does not support partial messages, the behavior degrades safely to the
previous single-event delivery.

---

## 5. Settings reference {#settings-reference}

Settings uses a two-column layout: a left sub-navigation list (5 groups, 18 pages)
and a right content pane. Click any page item to switch without a page reload.
A **quick-search** input at the top of the left nav filters pages by label in real
time. Advanced pages are marked with an "Advanced" badge.

The default landing page is **AI & provider**.

### Group: Essentials

The minimum configuration to get Synapse working.

| Page | Label | Contents |
|------|-------|----------|
| `providers` | **AI & provider** | LLM provider CRUD; CLI Subscription Auth sub-block |
| `appearance` | **Appearance** | Theme (Light / Dark / System), language (EN / IT) |
| `setup` | **Setup** | Context window size and budget-split bar chart; "Re-open setup wizard" button |

**AI & provider** lists all configured `provider_config` rows. Each row shows type,
model ID, and scope. Delete a provider with the row's Delete button (a warning is
shown if you are deleting the last provider). The **CLI Subscription Auth** block
(at the bottom of the page) is where you paste the `sk-ant-oat01-…` token from
`claude setup-token`.

**Appearance** controls the visual theme. Three options:

| Option | Behaviour |
|--------|-----------|
| **Light** | Always light palette |
| **Dark** | Always dark palette |
| **System** | Follows the OS appearance setting live — no reload needed |

CodeMirror (the wiki editor) and the sigma.js graph canvas both follow the resolved
theme automatically.

**Setup** — the context window selector (4K through 1M tokens). The token budget is
split 60 % conversation history / 20 % retrieved context / 5 % system prompt /
15 % generation headroom; the bar chart visualizes absolute token counts.
The "Re-open setup wizard" button re-launches the first-run guided wizard (backend
health check → provider → PDF extractor → done). Skippable at any step.

---

### Group: Content & sources

Where your data comes from and how the wiki is written.

| Page | Label | Contents |
|------|-------|----------|
| `sourceWatch` | **Watched sources** | Scheduled folder import (Source Watch) configuration |
| `clipper` | **Web clipper** | Chrome MV3 extension settings and token management |
| `pdf` | **PDF extraction** | PDF extractor selection (pypdf / Marker) and Marker connection settings |
| `generation` | **Wiki generation** | Conversation history length; overview language; auto-wikilink enrichment |
| `scenarios` | **Scenarios** | Five vault-purpose presets (Research, Reading, Personal Growth, Business, General) |

**Watched sources.** Synapse can scan a mounted folder on a schedule and import
new or changed documents automatically. To use it:

1. Add a bind-mount in `docker-compose.yml` (e.g. `./import:/import:ro`).
2. Enable the toggle on this page.
3. Enter the **container path** (e.g. `/import`) — not your host path.
4. Choose a frequency (15 min / 1 h / 6 h / daily).
5. Click **Run now** to test immediately.

The scan is non-recursive by default (set `IMPORT_SCAN_RECURSIVE=true` to traverse
subdirectories). Capped at 200 files and 60 seconds per tick (configurable).

**PDF extraction.** Switch between `pypdf` (no microservice needed) and `marker`
(Marker microservice required, higher quality for scanned/complex PDFs). The
**Marker service URL** and **Marker timeout** are also editable here without a
container restart. Settings are stored in the `app_config` Postgres table with a
Default/Custom badge showing whether an override is active.

**Wiki generation.** Conversation history length (2–20 messages sent to the model
per turn). Overview language: leave blank to let the AI detect the vault's dominant
language; set an ISO code (e.g. `it`, `en`) to fix it. Auto-wikilink enrichment
toggle: when on, the post-ingest pass scans related pages for missing `[[wikilinks]]`.

**Scenarios.** Apply a pre-written preset to `vault/purpose.md` and `vault/schema.md`.
Five presets: Research, Reading, Personal Growth, Business, General. Applying a
scenario overwrites both files — copy their contents elsewhere first if you have
hand-crafted them.

---

### Group: AI behaviour [Advanced]

How the AI retrieves and reasons. The defaults are fine to start; change these only
when you have a specific reason.

| Page | Label | Contents |
|------|-------|----------|
| `context` | **Context window** | Same as Setup — context window size and budget-split bar chart |
| `embeddings` | **Embeddings** | Vector-embeddings toggle and embedding-format selector |
| `webSearch` | **Web search** | SearXNG URL, categories, and max queries per Deep Research run |
| `automation` | **Automation** | Scheduled lint scan and domain backfill (frequency + Run now buttons) |
| `limits` | **Limits & budget** | Operator-class caps (review, lint, research bounds, cost alert threshold) |

**Embeddings.** Toggle vector embeddings on or off. When off, Qdrant is not
contacted; search degrades to Postgres lexical search. The **Embedding format**
selector (`ollama` or `openai`) adjusts the request/response shape when your
embedding endpoint uses the OpenAI API format. Changes apply to subsequent ingests
and queries — existing vectors are not removed or regenerated retroactively.

**Web search.** Configure the SearXNG instance URL. Required for Deep Research and
for the web-search toggle in Chat. The endpoint must be reachable from the backend
container (use `host.docker.internal` or a Tailscale IP in Docker deployments, not
`localhost`).

**Automation (Job scheduling).** Two scheduled background jobs:
- **Lint scan** — runs a scan on the configured frequency; applying fixes is always
  manual.
- **Domain backfill** — re-classifies untagged pages against the current domain
  vocabulary (`force=false` by default: skip already-tagged pages). Cheap on
  subsequent runs.

Only one run per job can be in flight; a second trigger is refused. The schedule
clock resets on backend restart.

---

### Group: Access & security

Who can reach Synapse and how to connect other tools.

| Page | Label | Contents |
|------|-------|----------|
| `security` | **Security & access** | Synapse bearer token (client copy) + Cloudflare Access service token |
| `apiMcp` | **MCP integrations** | Remote MCP HTTP surface toggle, token management, Claude Desktop snippet |

**Security & access** is described in depth in section 6 (Accessing Synapse from
outside your network) and section 7 (Authentication).

**MCP integrations.** The Synapse backend exposes a remote MCP server at
`/mcp/server`. This page shows the current token source, the allow-without-token
flag, and a ready-to-paste Claude Desktop JSON snippet. You can generate, rotate,
or clear the MCP bearer token from here without restarting the container. See
`DEPLOY.md §5` for full remote MCP setup.

---

### Group: System

| Page | Label | Contents |
|------|-------|----------|
| `costs` | **Costs** | Monthly cost rollup by provider and operation + alert indicator |
| `maintenance` | **Maintenance** | Vault maintenance actions |
| `about` | **About** | Backend version, build info |

**Costs** — the cost dashboard shows a monthly breakdown by provider type and
operation (ingest, chat, lint, deep research, review). A red alert indicator
appears when the month-to-date total exceeds `COST_ALERT_THRESHOLD_USD` (informational
only — no AI calls are blocked). Data comes from `GET /costs/summary`. Local Ollama
runs always show `$0.0000`.

**Domain vocabulary.** The comma-separated domain vocabulary is configured in the
**Automation** page (AI behaviour group) — not in a separate Advanced section. Enter
your domain names (e.g. `ServiceNow, SAM, Procurement`) and click Save. Empty = feature
dormant (no provider calls during ingest).

---

## 6. Accessing Synapse from outside your network {#external-access}

Synapse is designed for homelab and self-hosted deployments. The recommended network
posture is: **never expose the backend port directly to the public internet**. Use
one of the patterns below depending on your access model.

### Decision table

| Scenario | Recommended approach |
|----------|---------------------|
| Only you, on your home LAN | Direct access (`http://truenas.local:8000`), no external config needed |
| You and trusted devices away from home | **Tailscale** mesh — private, encrypted, no public endpoint |
| Shared or public access (clipper on any machine, iOS away from home) | **Cloudflare Tunnel** + **Cloudflare Access** |
| Obsidian LiveSync / backup services inside the homelab | Tailscale or direct LAN |

---

### Tailscale (private mesh access) {#tailscale}

Tailscale creates an encrypted WireGuard mesh between all your devices. Every node
gets a stable Tailscale IP (100.x.x.x) and, with MagicDNS enabled, a hostname
(e.g. `truenas.tail12345.ts.net`).

**To reach Synapse via Tailscale:**

1. Install the Tailscale client on the TrueNAS host and on your client devices
   (macOS, Windows, iOS, Android).
2. Join both devices to the same tailnet.
3. Access Synapse at `http://<tailscale-ip>:8000` or
   `http://<magicDNS-name>:8000`.

No port-forwarding rules, no firewall exceptions, and no public endpoint needed.
The traffic never leaves the Tailscale encrypted overlay.

**When to use Tailscale:** personal access from trusted devices (your laptop, your
phone, trusted family members). Tailscale is invisible to the public internet and
does not require Cloudflare Access service tokens.

**Limitation:** every device must have the Tailscale client installed. It is not
suitable for sharing Synapse with people who won't install a VPN client.

---

### Cloudflare Tunnel (public HTTPS endpoint) {#cloudflare-tunnel}

Cloudflare Tunnel creates an outbound-only connection from your TrueNAS host to
Cloudflare's edge, exposing a `https://` hostname without opening any inbound ports
on your router or firewall.

**To set it up:**

1. Create a tunnel in the Cloudflare Zero Trust dashboard
   (Networks → Tunnels → Create a tunnel).
2. Install the `cloudflared` daemon on your TrueNAS host (or as a Docker container).
3. Configure an ingress rule:
   ```
   Hostname: synapse.yourdomain.com
   Service:  http://localhost:8000
   ```
4. Add `https://synapse.yourdomain.com` to `CORS_ALLOW_ORIGINS` in your `.env`.
5. Restart the Synapse stack.

The tunnel terminates TLS at Cloudflare's edge and forwards plain HTTP to your
backend. Synapse receives only plain HTTP internally.

---

### Cloudflare Access (authentication at the edge) {#cloudflare-access}

Cloudflare Access (Zero Trust) puts an authentication gate **in front of** the
Cloudflare Tunnel before any request reaches Synapse. This is the recommended
production posture introduced in v1.3.9.

Every request to `https://synapse.yourdomain.com` must pass the Access gate. Two
authentication paths exist depending on the client type:

#### Browser / PWA (interactive login)

When you open Synapse in a browser and have not authenticated, Cloudflare Access
redirects you to its login page (One-time PIN sent to your email, or an IdP like
Google/GitHub). After login, Cloudflare sets a `CF_Authorization` cookie in the
browser. Subsequent same-origin requests carry that cookie automatically.

**Nothing to configure in Synapse for browser access.** Leave the service-token
fields in Settings blank.

#### Non-browser clients (service tokens) {#service-tokens}

The Tauri desktop app, the native iOS app, and the Chrome web clipper cannot
complete the interactive login flow. They have no browser session, no cookie jar,
and no way to redirect to the login page. They authenticate using a Cloudflare
Access **service token**: two custom headers that identify the client at the edge.

```
CF-Access-Client-Id:     <client-id>.access
CF-Access-Client-Secret: <client-secret>
```

**Creating a service token in Cloudflare Zero Trust:**

1. Go to **Zero Trust dashboard → Access → Service Auth → Service Tokens**.
2. Click **Create Service Token**. Give it a descriptive name (e.g. "Synapse Desktop").
3. Copy the **Client ID** and **Client Secret** — the secret is shown only once.
4. Go to **Access Applications**, open your Synapse application.
5. Add a policy:
   - **Action:** Service Auth
   - **Include → Service Token:** select the token you just created.
6. Save.

> **Recommendation:** create one service token per client (one for the desktop app,
> one for the iOS app, one for the Chrome clipper). That way you can revoke any
> single one without affecting the others.

**Configuring the service token in Synapse (desktop app and browser):**

1. Open **Settings → Access & security → Security & access**.
2. Scroll to the **Cloudflare Access (service token)** section.
3. Paste your **Client ID** into the "Client ID" field (e.g. `xxxxxxxx.access`).
4. Paste your **Client Secret** into the "Client Secret" field.
5. Click **Update**.

The credentials are stored in your browser's `localStorage` (or the desktop app's
local storage). Every API request from that client will include the
`CF-Access-Client-Id` and `CF-Access-Client-Secret` headers automatically, passing
the Cloudflare gate.

For the **Chrome web clipper**, the Client ID and Client Secret are entered in the
extension's Options page (right-click the Synapse icon → Extension options).

For the **native iOS app**, the fields are in the app's Settings screen.

**v1.3.10 — desktop app native HTTP.** Before v1.3.10, the Tauri desktop app sent
API calls through the WebKit WebView (`fetch()`). Because the desktop app's origin
is `tauri://localhost` and the backend is cross-origin, browsers include a CORS
preflight (`OPTIONS`) before the real request. Cloudflare Access rejects CORS
preflights that carry service-token headers with 403, making the desktop app
unusable behind CF Access. As of v1.3.10, the desktop app routes all API calls
through the **native Tauri HTTP client** (`@tauri-apps/plugin-http`), which operates
at the Rust layer and does not trigger CORS preflights. The service token is sent
on every request without any preflight overhead, and Cloudflare Access accepts it.
The web/PWA path is unchanged.

#### Remote MCP behind Cloudflare Access

The `/mcp/server` endpoint has its own independent bearer token (`MCP_AUTH_TOKEN`).
You have two options:

- **Claude Desktop** (uses a JSON config that supports custom headers): add both the
  MCP bearer token and the CF-Access headers in `mcpServers` config.
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

- **claude.ai remote MCP connector** (UI only accepts a Bearer token): add a
  Cloudflare Access policy with **Action = Bypass** scoped to `/mcp/server`. The
  MCP bearer token remains the gate for that path — no protection is lost, and the
  connector keeps working.

---

### The Synapse bearer token (`SYNAPSE_AUTH_TOKEN`) {#synapse-auth}

Synapse has its own optional application-level bearer token, independent of
Cloudflare Access. Set `SYNAPSE_AUTH_TOKEN` in your `.env` to require it on all
REST routes. When unset, authentication is disabled (backward-compatible with all
pre-v1.0 deployments).

**Browser/PWA:** when the server has a token and the client does not, the next API
call returns 401 and a token-entry overlay appears. Enter the token and the request
is retried automatically.

**Desktop app:** the Connect screen has an "Access token" field below the server URL.
Enter the token before clicking Connect.

**Updating the client copy.** If you rotate `SYNAPSE_AUTH_TOKEN` on the server
(edit `.env` → restart the container), the stored client token becomes stale. To
update it:

1. Open **Settings → Access & security → Security & access**.
2. Paste the new token into the "Update token" field.
3. Click **Update**. The token is saved to `localStorage` immediately — no server
   call is made.

> This UI **only updates the client copy**. To rotate the server-side token, edit
> `SYNAPSE_AUTH_TOKEN` in your `.env` and restart the container. There is no way to
> rotate the server token from the UI.

To disconnect (clear the client token): click **Clear** on the Security & access page.

**Exempt endpoints** (always reachable without a bearer token even when `SYNAPSE_AUTH_TOKEN`
is set):

| Endpoint | Notes |
|----------|-------|
| `GET /status` | Desktop Connect screen probe |
| `GET /health/detailed` | Monitoring probes |
| `GET /docs`, `GET /openapi.json` | API schema |
| `OPTIONS` (any path) | CORS preflight cannot carry a Bearer header |
| `/mcp/server/*` | MCP surface has its own token |
| `POST /clip` | Web-clipper ingress has its own `CLIP_TOKEN` |

---

## 7. Authentication summary {#auth-summary}

There are up to three independent authentication layers in a fully-secured deployment.
You may use any combination; each is optional and independent.

| Layer | What it is | Where you configure it |
|-------|-----------|----------------------|
| **Cloudflare Access** | Edge gate — blocks unauthenticated requests at the Cloudflare edge before they reach Synapse | Cloudflare Zero Trust dashboard + Settings → Security & access (service tokens for non-browser clients) |
| **Synapse bearer token** | Application gate — `SYNAPSE_AUTH_TOKEN` env var; the backend rejects all REST calls without a matching `Authorization: Bearer` header | `.env` → restart container; client copy updated in Settings → Security & access |
| **MCP bearer token** | Separate gate for the `/mcp/server` MCP surface | Settings → MCP integrations; or `MCP_AUTH_TOKEN` env var |

For a typical homelab deployment on TrueNAS with Cloudflare Tunnel:
- Cloudflare Access gates the public URL.
- Browser users authenticate via the interactive CF login.
- The desktop app, iOS app, and Chrome clipper authenticate with a CF service token
  (configured in Settings → Security & access / extension Options / iOS Settings).
- `SYNAPSE_AUTH_TOKEN` is optional but adds defense-in-depth.
- The MCP bearer token is required if you want to connect Claude Desktop or claude.ai.

---

## 8. Opening the wiki in Obsidian {#obsidian}

The `vault/wiki/` folder is a valid Obsidian vault. Open it directly in Obsidian:

1. In Obsidian, choose **Open folder as vault** and point it at `vault/wiki/`.
2. Obsidian reads the `[[wikilinks]]` and YAML frontmatter that Synapse generates.
3. Synapse auto-generates `vault/wiki/.obsidian/app.json` on startup so the vault
   opens in reading mode by default.

You can browse, annotate, and link pages in Obsidian. Synapse will not overwrite
your manual edits; it will regenerate a page only if you ingest the same source again.

**v1.3.5: structured frontmatter.** As of v1.3.5, every generated wiki page includes
`created` (preserved across regeneration) and `updated` (advances each write) in the
frontmatter, matching the full llm_wiki frontmatter contract.

**v1.3.5: log.md format.** `log.md` is now a narrative, day-grouped diary:
`## YYYY-MM-DD` headers with `- HH:MM:SSZ · <verb> · <type> · [[Title]] — path`
bullets. It remains append-only and machine-parseable. You can read it in Obsidian
as a human-readable history of what your vault contains.

---

## 9. Higher-quality PDF extraction with Marker {#marker-pdf}

By default Synapse uses **pypdf** to extract text from PDF files. For scanned PDFs,
multi-column layouts, or PDFs with embedded tables, switch to the **Marker**
extractor — an optional Marker microservice that runs a vision model pipeline on
your GPU.

**Switching at runtime (no restart needed):**

1. Open **Settings → Content & sources → PDF extraction**.
2. Change **PDF extractor** from `pypdf` to `marker`.
3. Confirm the **Marker service URL** matches your running microservice
   (default: `http://host.docker.internal:8555`).
4. Adjust **Marker timeout** if needed (default 120 s — generous for large scanned PDFs).
5. Click **Save** next to each field. A "Custom" badge confirms the override is active.

**Via the Convert section.** Drag up to 10 PDF files onto the Convert panel and click
**Convert & ingest**. Each file is processed by Marker explicitly; failures show an
inline error with no silent pypdf fallback. Successful conversions write
`.extracted.md` files to `vault/raw/sources/` which the watcher ingests automatically.

**Automatic watcher path.** When `PDF_EXTRACTOR=marker` and the watcher picks up a
PDF dropped into `vault/raw/sources/`, a Marker failure silently falls back to pypdf
and logs a `WARNING` — only the Convert section shows errors explicitly.

---

## 10. Image captions {#image-captions}

When `VISION_CAPTIONS_ENABLED=true` is set by the operator, Synapse generates an
AI-written caption for each ingested image file (`.png`, `.jpg`, `.jpeg`, `.webp`).
The caption becomes the wiki page body; captions are cached by SHA-256 so the same
image is never captioned twice.

This requires the active provider to report `supports_vision=true`. When vision is
not supported or when the flag is `false` (the default), image files produce a stub
placeholder page.

---

## 11. Audio and video transcription {#av-transcription}

When `AV_TRANSCRIPTION_ENABLED=true` is set by the operator, Synapse transcribes
audio and video files (`.mp3`, `.m4a`, `.wav`, `.mp4`, `.mov`, `.webm`) using a
host-side Whisper microservice before ingest.

Transcription is bounded by `AV_MAX_FILES_PER_RUN` (default 10) per ingest trigger.
The Whisper microservice URL is configured via `WHISPER_SERVICE_URL`.
Cost: zero for transcription (Whisper runs locally on your GPU).

---

## 12. Vault export and backup {#export-backup}

```bash
# Vault filesystem snapshot (ZIP of raw/ + wiki/ + purpose.md + schema.md + .obsidian/)
curl -f http://localhost:8000/export \
     -H "Authorization: Bearer $SYNAPSE_AUTH_TOKEN" \
     -o synapse-vault-$(date +%Y%m%d).zip

# Database metadata snapshot (pages, links, edges, ingest runs, review items)
curl -f http://localhost:8000/export/data.json \
     -H "Authorization: Bearer $SYNAPSE_AUTH_TOKEN" \
     -o synapse-data-$(date +%Y%m%d).json
```

The ZIP is named `synapse-vault-{vault_id}-{date}.zip` and caps at 500 MB
uncompressed. The JSON object contains: `pages`, `links`, `edges`, `runs`,
`review_items`, `exported_at`, `data_version`.

See `DEPLOY.md §17` for restore paths (vault-directory-only re-ingest or full
Postgres volume restore).

---

## 13. Domain vocabulary and auto-tagging {#domain-sections}

Domain sections let you slice the wiki by subject area (e.g. ServiceNow, SAM,
Procurement, Regolamentazioni, TPRM).

**Defining the vocabulary:**

1. Open **Settings → AI behaviour → Automation**.
2. Find **Domain vocabulary** in the job-scheduling card area.
3. Enter your domain names as a comma-separated list, e.g.:
   `ServiceNow, SAM, Procurement, Regolamentazioni, TPRM`
4. Click **Save**.

**Auto-tagging on ingest:** when the vocabulary is non-empty, the ingest pipeline
runs one bounded provider call per page after it has been written. The provider
returns which vocabulary terms match; these are written to `pages.tags` as
`domain/<Name>` entries — they round-trip through YAML frontmatter and appear in
Obsidian's tag pane under the `domain/` group.

**Backfilling existing pages:**

```bash
# Start a bounded background backfill (202 Accepted)
curl -X POST http://localhost:8000/ops/backfill-domains \
     -H "Authorization: Bearer $SYNAPSE_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{}'
```

Default bounds: 500 pages / 60 000 tokens. To re-classify all pages (e.g. after
updating the vocabulary):

```bash
curl -X POST http://localhost:8000/ops/backfill-domains \
     -H "Authorization: Bearer $SYNAPSE_AUTH_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"max_pages": 2000, "force": true}'
```

Check status: `GET /ops/backfill-domains`.

You can also schedule the backfill via **Settings → AI behaviour → Automation** so
new/untagged pages are classified automatically.

---

## 14. Web Clipper {#web-clipper}

The Synapse web clipper is a Chrome MV3 browser extension. It lets you clip any web
page into your vault directly from the browser.

**Installing the extension:**

1. Download `synapse-clipper-<version>.zip` from the GitHub releases page.
2. Extract the zip to a folder on your computer.
3. Open Chrome → `chrome://extensions/` → enable **Developer mode**.
4. Click **Load unpacked** and select the extracted folder.
5. The Synapse icon appears in the browser toolbar.

**Configuring the extension (required before first use):**

1. Right-click the Synapse icon → **Extension options**.
2. Set the **Synapse Base URL** to your backend (e.g. `https://synapse.yourdomain.com`
   or `http://localhost:8000`).
3. Set the **Clip Token** to the value in Settings → Content & sources → Web clipper
   (or from your `CLIP_TOKEN` env var).
4. If you are behind **Cloudflare Access**, also paste your **CF-Access Client ID**
   and **CF-Access Client Secret** in the extension Options.
5. Click **Save** and optionally **Test Connection**.

**Clipping a page:**

1. Navigate to any web page you want to add to your vault.
2. Click the Synapse extension icon.
3. Review the title and Markdown preview (converted via Mozilla Readability +
   Turndown).
4. Optionally edit the title.
5. Click **Clip**. The extension sends the page to `POST /clip`, which writes it to
   `vault/raw/sources/` and triggers normal watcher-based ingest (I1).
6. The page appears in the Sources section within 15–30 seconds.

The backend must have `CLIP_ENABLED=true` and a matching `CLIP_TOKEN` set. The
extension's extension ID must be added to `CLIP_ALLOWED_ORIGINS`.

---

## 15. Mobile and PWA {#mobile-pwa}

On screens narrower than 768 px, the navigation rail collapses to an icon-only strip
(labels hidden). The three-panel Wiki section switches to a vertical stack on mobile.
The sigma.js canvas supports pinch-to-zoom and two-finger pan on touch screens.

**Install as PWA:**

- **iOS (Safari 16.4+):** tap Share → **Add to Home Screen**.
- **Android (Chrome):** tap the browser menu → **Install app**.

Once installed, Synapse opens in standalone mode with no browser chrome. The service
worker caches the app shell for instant load. All API calls are network-first (never
served from cache — data is always fresh, preserving the `dataVersion` model).

---

## 16. Desktop app (Tauri v2) {#desktop-app}

The Synapse desktop app is a native Tauri v2 window for macOS and Windows. Download
the latest installer from the GitHub releases page. See `DEPLOY.md §7` for install
instructions, unsigned-binary warnings, and build-from-source steps.

**v1.3.10 native HTTP.** The desktop app now routes all API calls through the Tauri
native HTTP client (`@tauri-apps/plugin-http`) instead of the WebKit `fetch()`. This
eliminates CORS preflights that Cloudflare Access would reject, making the desktop
app fully functional behind a Cloudflare Tunnel + Access gateway when the service
token is configured in Settings. The web/PWA path is unchanged.

### Connect screen (first launch)

The first time you open the app (and after **Change server**), you see a full-screen
**Connect** screen.

- Enter the base URL of your backend (scheme required, no trailing slash).
- If `SYNAPSE_AUTH_TOKEN` is set on the server, paste the token in the "Access token"
  field below the URL.
- Click **Connect**. The app sends a `GET /status` probe; on 2xx the URL is saved
  and the full interface loads.

| Backend location | URL to enter |
|-----------------|--------------|
| Same machine | `http://localhost:8000` |
| TrueNAS via Tailscale | `http://truenas:8000` |
| Cloudflare Tunnel | `https://synapse.yourdomain.com` |

### Server chip

Once connected, the header shows a chip with the backend hostname. Click it for a
dropdown of up to the last 5 connected servers, plus **Change server**. Switching
servers triggers a full page reload to flush any cross-server cached state.

### Desktop-only features

| Feature | Shortcut / behaviour |
|---------|---------------------|
| **Zoom** | Cmd/Ctrl+/Cmd/Ctrl−/Cmd/Ctrl+0 — 80–140 %, persisted across restarts |
| **Ingest notifications** | Native OS notification on ingest completion; permission requested on first fire |
| **Auto-update** | Checks `latest.json` on GitHub on every launch; shows a banner with "Update now" / "Later"; verifies minisign signature before installing |

---

## 17. Native iOS app {#ios-app}

As of v1.3.8, Synapse ships a native **SwiftUI** iOS app (iOS 17+) in `ios/`. It
connects to the same backend REST API and provides five tabs:

| Tab | Contents |
|-----|----------|
| **Wiki** | Vault stats, recent and all pages, page detail (Markdown body, links, mini-graph) |
| **Search** | 4-phase RAG search with type filters |
| **Chat** | Streaming NDJSON chat with tappable citations |
| **Graph** | Interactive knowledge graph with pan/zoom |
| **More** | Review queue, import, deep research, settings (server URL + token, appearance, provider) |

**Distribution.** The app is **not on the App Store**. Each GitHub release includes
an unsigned `.ipa` file (`Synapse-<version>-unsigned.ipa`). You sign it with your
Apple ID using AltStore, Sideloadly, or Xcode. With a free Apple developer account,
the signed app is valid for 7 days; with a paid account, 1 year.

**Cloudflare Access.** As of v1.3.9, the iOS app supports Cloudflare Access service
tokens. Configure the Client ID and Client Secret in the app's Settings screen.

Build instructions: `ios/README.md` and `ios/build-unsigned-ipa.sh`.

---

## 18. Status bar

The status bar at the bottom of every section shows:

- **Vault name** (e.g. `default`)
- **Data version** (e.g. `v42`) — increments on every page write
- **Uptime** — how long the backend has been running
- **Active provider**

---

## 19. Error recovery

Each section is wrapped in a `SectionErrorBoundary`. If a section encounters an
unexpected error (e.g. a malformed API response), only that section shows an error
message with a **Retry** button. The rest of the app continues to work normally.
Clicking Retry remounts the failed section and re-fetches its data.

---

## 20. Runtime settings (no restart needed) {#runtime-settings}

Nine behaviour settings can be changed from the Settings panel without editing `.env`
or restarting Docker. Overrides are stored in the Postgres `app_config` table and
take effect immediately. Each field shows a Default (grey) or Custom (teal) badge.
Click **Reset to default** next to any field to remove the override and revert to
the env-var baseline.

| Setting | Location in Settings | Default |
|---------|---------------------|---------|
| PDF extractor | Content & sources → PDF extraction | `pypdf` |
| Marker service URL | Content & sources → PDF extraction | `http://host.docker.internal:8555` |
| Marker timeout (s) | Content & sources → PDF extraction | `120` |
| Monthly cost alert (USD) | System → Costs | unset |
| Vector embeddings on/off | AI behaviour → Embeddings | On |
| Embedding format | AI behaviour → Embeddings | `ollama` |
| Overview language | Content & sources → Wiki generation | (auto) |
| Auto wikilink enrichment | Content & sources → Wiki generation | On |
| Domain vocabulary | AI behaviour → Automation | (empty) |

> **Infrastructure settings (DATABASE_URL, QDRANT_URL, VAULT_PATH, SYNAPSE_AUTH_TOKEN,
> etc.) are env-var-only and cannot be changed from the UI** — they require a container
> restart and could cause data corruption if changed while the service is live.
