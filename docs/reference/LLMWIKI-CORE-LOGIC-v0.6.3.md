# nashsu/llm_wiki v0.6.3 — Authoritative Core-Logic Reference

> Extracted 2026-07-14 from a fresh clone of https://github.com/nashsu/llm_wiki at v0.6.3
> (`git clone --depth 50`; HEAD `9b71ade` "release: v0.6.3 fix ingest lifecycle issues").
> This is the behavioral spec for Synapse 1.7.0 parity (Ingest / Review / Lint / onboarding).
> Line numbers refer to the v0.6.3 tree. Do not "improve" behaviors here without an ADR.

## 0. Architectural facts to internalize first

- llm_wiki is a Tauri (Rust) + React/TS desktop app. All LLM calls go through
  `src/lib/llm-client.ts::streamChat` — **SSE text streaming only; there is no JSON /
  response_format mode anywhere**. JSON, when needed, is requested in-prompt and parsed with
  tolerant balanced-brace extraction.
- Two ingest paths exist. `autoIngest()` (`src/lib/ingest.ts:569`) is the fully-automated 2-stage
  pipeline used by the queue and by chat "Save to Wiki" — **this is the authoritative behavior**.
  `startIngest()`/`executeIngestWrites()` (`ingest.ts:3076/3166`) is the interactive chat-driven
  variant (looser parsing, known schema/purpose path bug) — not the parity target.
- **`enrich-wikilinks.ts` is dead code in the shipping pipeline** — its only importers are tests
  and test-helpers (verified by grep). Wikilinks are produced ONLY inline by the LLM during
  generation; link density comes from the prompts.
- `schema.md` / `purpose.md` live at the **project root** (not under `wiki/`). `autoIngest`
  reads them from root (`ingest.ts:692-698`).
- Ingest LLM options are always `temperature: 0.1`, `reasoning: { mode: "off" }`.

## 1. INGEST — exact pipeline

### 1.1 Queue (`src/lib/ingest-queue.ts`)

Task shape: `{ id, projectId, sourcePath ("raw/sources/…"), folderContext, status:
pending|processing|done|failed, addedAt, error, retryCount }`.

- **Concurrency = strictly 1 (sequential), FIFO** — `processNext` returns if already processing
  (`:659`); first pending task wins (`:675`).
- Persistence `.llm-wiki/ingest-queue.json` (only non-done tasks). Done tasks removed on success.
- **Dedup on enqueue** (`upsertQueuedIngestTask` `:124`): pending/failed task for the same
  normalized source path is reused (reset to pending, retryCount=0), never duplicated.
- **Retries: MAX_RETRIES = 3** (`:610`). Empty output is a failure: after `autoIngest`,
  `if (writtenFiles.length === 0) throw "Ingest produced no output files"` (`:764`).
- **Cancellation** (`:303`): abort in-flight controller, then `cleanupWrittenFiles` →
  cascade-delete each written file (file + vector chunks), skipping structural pages.
- **429/rate-limit handling** (`:621`): `/\b429\b|rate[_\s-]*limit|usage\s+limit|quota|too many
  requests/i` → auto-pause with **15-minute auto-resume**.
- Restore on startup: `processing` → `pending`, but restored tasks are NOT auto-run (no surprise
  spend). Stale-context guard: every await re-checks the current project id and bails on switch.
- **On queue drain** (`onQueueDrained` `:636`): if any task was processed since last drain, run
  `sweepResolvedReviews`. This is the ONLY automatic trigger of the review sweep.

### 1.2 `autoIngestImpl` steps (`ingest.ts:626-1326`) — under a per-project mutex

1. Optional MinerU PDF preprocessing (opt-in, cached, falls back to built-in pdfium).
2. Read context in parallel: source text, root `schema.md` + `purpose.md`, `wiki/index.md`,
   `wiki/overview.md`.
3. **Cache check** (§1.7). Hit → skip generation, return cached list, status "Skipped (unchanged)".
4. Image extraction to `wiki/media/<source-slug>/` + optional vision captioning (SHA-256-keyed
   cache); if captioning disabled, image refs are stripped from the source content.
5. **Long-source chunking** if content > source budget (§1.6).
6. **Stage 1 — Analysis**: `streamChat(buildAnalysisPrompt…, { temperature: 0.1, reasoning: off,
   max_tokens: 4096 })`. Skipped if the long-source path precomputed it.
7. **Stage 2 — Generation**: `streamChat(buildGenerationPrompt…, { temperature: 0.1, reasoning:
   off, max_tokens: computeIngestGenerationMaxTokens(...) })`. User message forces the response
   to begin with `---FILE:`.
8. **Conditional dedicated review stage** (`shouldRunDedicatedReviewStage` `:2036`): runs only if
   generation length ≥ **10,000** chars OR ≥ **4** FILE blocks OR a `---REVIEW:` block already
   present. Uses `buildReviewSuggestionPrompt`.
9. **Write files** (`writeFileBlocks`, §1.5).
10. **Deterministic index update** (§1.8) — code, never LLM.
11. Aggregate repair: ONLY `wiki/log.md` may be LLM-repaired (`:1427`); index/overview never.
12. **Fallback source summary** if no `wiki/sources/<slug>.md` was written and not aborted.
13. Parse review items from BOTH generation and review-stage outputs → review store.
14. Save cache only if `writtenPaths.length > 0 && hardFailures.length === 0`.
15. Embeddings for each non-aggregate written page (if enabled).

### 1.3 Page types (`src/lib/wiki-page-types.ts`)

`GENERATION_WIKI_TYPES = source, entity, concept, comparison, query, synthesis, thesis,
methodology, finding`. Dir map: `entities→entity, concepts→concept, sources→source,
queries→query, comparisons→comparison, synthesis→synthesis, findings→finding, thesis→thesis,
methodology→methodology` (+ `overview` for overview.md; custom dirs fall back to the dir name).
Per-type creation is driven ENTIRELY by the generation prompt + the project schema routing —
no hardcoded type logic.

### 1.4 Prompts (verbatim structure; port 1:1)

**Analysis** — `buildAnalysisPrompt(purpose, index, sourceContent, schema)` (`ingest.ts:2046-2102`),
sections joined with `.filter(Boolean).join("\n")`:

```text
You are an expert research analyst. Read the source document and produce a structured analysis.
Do not output chain-of-thought, hidden reasoning, or a thinking transcript. Reason internally and write only the concise final analysis.

<languageRule(sourceContent)>

Your analysis should cover:

## Key Entities
List people, organizations, products, datasets, tools mentioned. For each:
- Name and type
- Role in the source (central vs. peripheral)
- Whether it likely already exists in the wiki (check the index)

## Key Concepts
List theories, methods, techniques, phenomena. For each:
- Name and brief definition
- Why it matters in this source
- Whether it likely already exists in the wiki

## Main Arguments & Findings
- What are the core claims or results?
- What evidence supports them?
- How strong is the evidence?
- Which named subject is each claim about? Do not transfer claims, limits, or evaluations from one entity/model/product/method to another just because they share keywords.

## Connections to Existing Wiki
- What existing pages does this source relate to?
- Does it strengthen, challenge, or extend existing knowledge?

## Contradictions & Tensions
- Does anything in this source conflict with existing wiki content?
- Are there internal tensions or caveats?

## Recommendations
- What wiki pages should be created or updated?
- If the project schema (below) defines page types beyond entity/concept (e.g. goal, habit, reflection, finding, decision, meeting), and the source genuinely contains matching content, recommend pages of those types — name the type explicitly. Only when the source actually supports it; never invent goals/habits/journal entries that aren't in the source.
- What should be emphasized vs. de-emphasized?
- Any open questions worth flagging for the user?

Be thorough but concise. Focus on what's genuinely important.

If a folder context is provided, use it as a hint for categorization — the folder structure often reflects the user's organizational intent (e.g., 'papers/energy' suggests the file is an energy-related paper).

## Project Schema (page types available — map source content to schema-defined types when it fits)
<schema>
## Wiki Purpose (for context)
<purpose>
## Current Wiki Index (for checking existing content)
<index>
```

Analysis user message: `Analyze this source document:\n\n**File:** <sourceIdentity>[\n**Folder
context:** <folderContext>]\n\n---\n\n<sourceContext>`.

**Generation** — `buildGenerationPrompt(schema, purpose, index, sourceFileName, overview,
sourceContent, sourceSummaryPath)` (`ingest.ts:2107-2274`). `<today>` = local `YYYY-MM-DD`.
Key normative sections, in order:

```text
You are a wiki maintainer. Based on the analysis provided, generate wiki files.
Do not output chain-of-thought, hidden reasoning, or explanatory preamble. Reason internally and output only the requested FILE/REVIEW blocks.

<languageRule(sourceContent)>

## IMPORTANT: Source File
The original source file is: **<sourceFileName>**
All wiki pages generated from this source MUST include this filename in their frontmatter `sources` field.
Today's date is **<today>**. Use this exact date for all new `created`, `updated`, and wiki/log.md ingest dates.

## Project Schema and Routing (AUTHORITATIVE)
<schema>

Use this schema as the primary routing rule for page types and directories.
If it defines custom folders or distinctions (for example people, technologies, organizations, methods, or cases), write pages into those schema-defined folders instead of forcing them into wiki/entities/ or wiki/concepts/.
Use wiki/entities/ and wiki/concepts/ only when the schema does not provide a more specific destination.
Every generated page's frontmatter type must match the schema directory used in its FILE path.

## What to generate

1. A source summary page at **<summaryPath>** (MUST use this exact path)
2. Entity or schema-defined typed pages for key named things identified in the analysis. Prefer schema-defined directories when present; otherwise use wiki/entities/.
3. Concept or schema-defined typed pages for key ideas, methods, techniques, and abstractions. Prefer schema-defined directories when present; otherwise use wiki/concepts/.
4. A log entry for wiki/log.md (just the new entry to append, format: ## [YYYY-MM-DD] ingest | Title)
Do not generate wiki/index.md or wiki/overview.md. The application maintains aggregate navigation separately so large wikis are never rewritten through model output.

## Frontmatter Rules (CRITICAL — parser is strict)
[1. first line exactly `---`, no ```yaml fence, no `frontmatter:` prefix; 2. key: value per line;
 3. closing `---`; 4. body follows; 5. arrays inline `[a, b, c]`; wikilinks belong in the BODY
 only — never `related: [[a]], [[b]]`; write `related: [a, b]` with bare slugs.]

Required fields and types:
  • type     — one of the known types (source | entity | concept | comparison | query | synthesis | thesis | methodology | finding), or a custom type explicitly defined by the project schema
  • title    — string (quote it if it contains a colon)
  • created  — <today> (YYYY-MM-DD, no quotes)
  • updated  — <today> (same as created)
  • tags     — array of bare strings: `tags: [microbiology, ai]`
  • related  — array of bare wiki page slugs: `related: [foo, bar-baz]` (no wiki/, no .md, no [[…]])
  • sources  — array of source filenames; MUST include "<sourceFileName>".

[Concrete parseable example page follows in the original — entity with tags/related/sources and a
 body starting `# Example Entity` and prose with [[wikilink]] usage.]

Other rules:
- Use [[wikilink]] syntax in the BODY for cross-references between pages
- If you include images, use wiki-root-relative paths such as `media/source-slug/image.png`; never output absolute filesystem paths.
- Preserve subject boundaries: when a source discusses multiple entities/models/products/methods, keep claims, evaluations, limitations, benchmark results, and recommendations attached to the exact subject they describe.
- Do not merge or generalize a claim about one subject into another subject's page solely because they share terms.
- If a page needs to mention another subject for comparison, write it explicitly as a comparison and cite which source/frontmatter `sources` entry supports that statement.
- Use kebab-case filenames
- Derive filenames from the page title in the mandatory output language, but short proper nouns and technical identifiers take precedence (OpenAI, GPT-5, Transformer, CLIP, ImageNet, PyTorch, CUDA, GitHub, arXiv, React, …). No raw URLs/citations in paths. CJK prose titles keep readable CJK characters in the filename.
- Follow the analysis recommendations on what to emphasize
- If the analysis found connections to existing pages, add cross-references

## Review block types
[contradiction | duplicate | missing-page | suggestion — "Only create reviews for things that
 genuinely need human input. Don't create trivial reviews."]

## OPTIONS allowed values (only these predefined labels):
- contradiction/duplicate/missing-page/suggestion: OPTIONS: Create Page | Skip
[Deep Research is auto-added by the system client-side. Do NOT invent custom option labels.]
For suggestion and missing-page reviews, the SEARCH field must contain 2-3 web search queries
(keyword-rich, specific — NOT titles or sentences), separated by ` | `.

## Wiki Purpose
<purpose>
## Current Wiki Index (preserve all existing entries, add new ones)
<index>
## Current Overview (update this to reflect the new source)
<overview>

## Output Format (MUST FOLLOW EXACTLY — this is how the parser reads your response)
---FILE: wiki/path/to/page.md---
(complete file content with YAML frontmatter)
---END FILE---
[REVIEW template: ---REVIEW: type | Title--- description, OPTIONS:, PAGES:, SEARCH: ---END REVIEW---]

## Output Requirements (STRICT — deviations will cause parse failure)
1. The FIRST character of your response MUST be `-` (the opening of `---FILE:`).
2-6. No preamble; do not echo the analysis; no tables/lists/headings outside blocks; no trailing
commentary; only blank lines between blocks.
7. FILE block prose must use the mandatory output language; preserve proper nouns/technical terms.
If you start with anything other than `---FILE:`, the entire response will be discarded.

---
<languageRule(sourceContent)>
```

`summaryPath` = `sourceSummaryPath ?? wiki/sources/<sourceBaseName>.md`. Schema/purpose/index/
overview sections included only when non-empty. Generation user message: Stage-1 analysis under
"## Stage 1 Analysis (context only — do not repeat)" + "## Source Context" + force `---FILE:` start.

**Dedicated review stage** — `buildReviewSuggestionPrompt` (`ingest.ts:2276-2333`): "Your job is
NOT to generate wiki pages… Output only REVIEW blocks… Prefer 1-5 high-signal reviews. If there
is nothing worth reviewing, output nothing." SEARCH line required for suggestion/missing-page.
Budget caps: `sectionCap = max(4000, floor(maxCtx*0.15))`, `indexCap = max(3000, floor(sectionCap*0.8))`.
Inputs: purpose, trimmed index, source identity, trimmed analysis, trimmed source, trimmed generation.

### 1.5 `writeFileBlocks` (`ingest.ts:1783-1956`) — write rules, in order per block

1. Path under `wiki/sources/` and a `sourceSummaryPath` is set → **force** that exact path.
2. `wiki/index.md` / `wiki/overview.md` (case-insensitive) → **drop with warning** (app-managed).
3. `sanitizeIngestedFileContent` (§1.5.2).
4. Date stamping: log → `stampGeneratedLogDate`; non-listing → created/updated = today.
5. Non-log/non-listing → `canonicalizeSourcesField` (§1.5.3).
6. Source summary → rewrite `](media/…` → `](../media/…`.
7. CJK output language → re-slug filename from title if the LLM slug isn't CJK.
8. **Schema routing validation** (`validateWikiPageRouting`) → drop if frontmatter `type` doesn't
   match the schema table's dir for that path.
9. **Language guard**: concept-style pages (NOT log, NOT `/entities/` or `/sources/`), when
   outputLanguage ≠ "auto" and content doesn't match → drop with warning.
10. Write by kind: **log** → append `existing + "\n\n" + content.trim()`; **listing** → overwrite
    (already dropped for the two app-managed); **content page** → merge via `mergePageContent`
    (§1.10), `replaceExistingBody=true` iff existing page is owned solely by this source.

Returns `{ writtenPaths, warnings, hardFailures }`; only FS errors are hard failures (block cache
save); soft drops don't.

#### 1.5.1 `parseFileBlocks` (`ingest.ts:454-547`)
- CRLF→LF; line-based state machine.
- Opener `/^---\s*FILE:\s*(.+?)\s*---\s*$/i`; closer `/^---\s*END\s+FILE\s*---\s*$/i`.
- CommonMark fence tracking `/^\s{0,3}(```+|~~~+)/` so an `---END FILE---` inside a fenced block
  does not close.
- Unclosed at EOF → dropped ("likely truncation"); empty path → dropped; unsafe path → dropped
  (`isSafeIngestPath`: must be under `wiki/`, no `..`, no absolute/drive, Windows-safe segments,
  no control chars).

#### 1.5.2 `sanitizeIngestedFileContent` (`src/lib/ingest-sanitize.ts`) — exactly 4 rules
1. Strip outer code fence (```yaml/md/markdown/bare, first + matching last line).
2. Strip a leading `frontmatter:` line immediately followed by `---`.
3. Add missing opening `---` when content starts with a frontmatter key
   (`type|title|created|updated|tags|related|sources`) and a `---` appears within 30 lines
   before any `#` heading.
4. Inside the FM block only: `key: [[a]], [[b]]` → `key: ["[[a]]", "[[b]]"]`.

#### 1.5.3 `canonicalizeSourcesField` (`ingest.ts:1539-1567`)
Filter to valid refs (no absolute/`..`, not index/overview/log, not `.llm-wiki`); canonicalize to
source identity (path relative to `raw/sources/`); **guarantee the active identity is present**;
case-insensitive dedup; always write inline `["a","b"]`.

### 1.6 Long-source chunking (`analyzeLongSourceInChunks`, `ingest.ts:2719-2847`)
Constants: `LONG_SOURCE_CHUNK_MIN=12000`, `MAX=60000`, `DIGEST_MAX=15000`,
`CHUNK_ANALYSIS_MAX=40000`. `targetChars = clamp(floor(budget*0.55), 12000, 60000)`,
`overlapChars = clamp(floor(target*0.08), 800, 3000)`. Semantic split on heading/paragraph
boundaries with overlap; per-chunk `streamChat` (max_tokens 4096) emitting `## Chunk Analysis` +
`## Updated Global Digest`; checkpointed and resumable; final consolidated analysis+context
replace the raw source for Stages 1-2.

### 1.7 Cache (`src/lib/ingest-cache.ts`)
`.llm-wiki/ingest-cache.json`, keyed by source identity. Entry `{ hash: SHA-256(extracted text),
timestamp, filesWritten[] }`. Valid hit = hash match AND every listed file still exists.

### 1.8 index.md / log.md / overview.md
- **index.md by code only** (`updateWikiIndexDeterministically`, `ingest.ts:1432-1464`): for each
  new non-aggregate page whose slug isn't already a `[[…]]` target in index.md, append
  `- [[<slug>]] — <title>` into a bounded `## Recently Updated` section; dedup; **cap 200**.
- **log.md** appended via the generation's log entry (`## [YYYY-MM-DD] ingest | Title`);
  LLM-repairable if missing.
- **overview.md is never regenerated by the pipeline** (created at vault init only).

### 1.9 Wikilinks
Produced ONLY inline by the LLM during generation. `enrich-wikilinks.ts` is unwired (tests only).
For the record, its algorithm (a deterministic spec if ever needed): LLM returns strict JSON
`{links: [{term, target}]}`; term must be a literal case-sensitive substring; one entry per
target (first mention); v0.6.3 filters targets against actual on-disk `.md` slugs
(NFKC+lowercase normalization; case-colliding slugs dropped entirely); apply = first unlinked
literal occurrence, `[[term]]` if term==target else `[[target|term]]`.

### 1.10 Page merge (`src/lib/page-merge.ts`)
`UNION_FIELDS=[sources,tags,related]`, `LOCKED_FIELDS=[type,title,created]`,
`BODY_SHRINK_THRESHOLD=0.7`.
- No existing page → as-is. Byte-identical → existing.
- Always union array fields first.
- `replaceExistingBody` (owned-only): back up existing, keep array-merged content, restore LOCKED
  fields, stamp updated=today — **no LLM**.
- Old body == array-merged body → skip LLM.
- Else LLM merge (temperature 0.1; system prompt: preserve every factual claim from both versions,
  dedupe, keep subject/source attribution exact, first char `-`, output complete file).
- **Reject** LLM output if no frontmatter OR body < 0.7 × max(oldBodyLen,newBodyLen) → fall back
  to array-merged. On accept: restore LOCKED fields, re-union arrays, stamp updated.
- Backups: `.llm-wiki/page-history/<sanitized-path>-<timestamp>`.

### 1.11 Model calls, budgets, language
- Providers: openai, anthropic, google, azure, ollama, minimax, custom (HTTP SSE) + claude-code,
  codex-cli (subprocess). 30-min backstop timeout. Reasoning-only responses (no content ≥200
  chars) are an explicit error.
- **Context budgets are in CHARACTERS** (`context-budget.ts`, default maxContextSize 204800):
  response reserve 15%, index 5%, pages 50%, per-page cap 30% (floor 5000).
  `computeIngestSourceBudget` = clamp derived from these into `[8000, min(300000, floor(maxCtx*0.6))]`.
- Generation max_tokens tiers (`:2427`): 8192 (<128K), 16384 (≥128K), 24576 (≥256K), 32768 (≥512K);
  analysis 4096; review/aggregate `min(8192, max(4096, floor(genTokens/2)))`.
- Language: `buildLanguageDirective` emits a "⚠️ MANDATORY OUTPUT LANGUAGE" block; language =
  configured outputLanguage unless "auto", then `detectLanguage` (Unicode script counting).

## 2. REVIEW — exact model

- Item types: `contradiction | duplicate | missing-page | confirm | suggestion`.
- **Content-stable id**: `review-<FNV1a32hex(type + "::" + normalizeReviewTitle(title))>`.
  `normalizeReviewTitle` strips `missing page:` / `duplicate page:` (+ CJK variants) prefixes,
  collapses whitespace, lowercases. Same (type, normalized title) ⇒ same id ⇒ dedup; **resolved
  state survives re-ingest**; merge unions affectedPages/searchQueries, keeps earliest createdAt,
  prefers non-empty description, resolved wins.
- Sources of items: (1) `---REVIEW:` blocks from generation + dedicated review stage
  (`parseReviewBlocks`: regex `---REVIEW:\s*(\w[\w-]*)\s*\|\s*(.+?)\s*---` … `---END REVIEW---`;
  unknown type → `confirm`; OPTIONS split on `|`, default `[Approve, Skip]`; PAGES on `,`;
  SEARCH on `|`); (2) Lint "Send to Review".
- **Sweep** (`sweep-reviews.ts`), runs ONLY on queue drain:
  - Stage 1 rules: `missing-page` → resolve if any candidate (normalized title ≤100 chars, or
    affectedPages basenames) matches an existing page (byId exact, byId spaces→`-`, byTitle exact,
    lowercased). `duplicate` → resolve if affectedPages non-empty AND not all still exist.
    `contradiction`/`suggestion`/`confirm` → never rule-resolved.
  - Stage 2 LLM judge: batches of 40, max 5 batches, ≤300 pages listed in prompt, asks for
    `{"resolved": [ids]}`, "Be conservative", **early-break when a batch resolves nothing**.
- UI actions: Deep Research auto-added for suggestion/missing-page (requires search config;
  queues research with the item's searchQueries). **Create Page = deterministic draft stub**:
  `# <title>` + description body, `type` via keyword detection (entity/concept/comparison/
  synthesis keywords EN+CN; missing-page→concept; contradiction/suggestion→query; default query),
  filename `<slug>-<YYYY-MM-DD>-<HHMMSS>.md` (slug NFKC kebab, cap 50 chars), dirs
  entities/concepts/comparisons/synthesis/queries; then index.md updated + log.md appended.
  Skip/dismiss → plain resolve. Persistence `.llm-wiki/review.json`.

## 3. LINT — exact checks and fixes

- **One pass, user-triggered, no loop, no auto-run.** "Semantic" checkbox gates the LLM stage.
- Structural (`runStructuralLint`, deterministic, all `wiki/**/*.md` except index/log):
  - `orphan` (info): 0 inbound wikilinks (case-insensitive slug match) + `suggestedSource`
    (best token-overlap page).
  - `no-outlinks` (info): zero `[[…]]` in page + `suggestedTarget`.
  - `broken-link` (warning): `[[target]]` not in slug map (full rel-slug OR basename, lowercased);
    `suggestedTarget` attached if similarity ≥ **0.74**.
  - Wikilink regex: `/\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]/g`. Scoring constants:
    `SAME_BASENAME_SCORE=0.96`, `CONTAINS_TARGET_SCORE=0.82`, `RELATED_PAGE_SUGGESTION_MIN_SCORE=0.08`,
    `SAME_FOLDER_SCORE_BONUS=0.08`, `SINGLE_CJK_TOKEN_WEIGHT=0.35`, token window 4000.
- Semantic (`runSemanticLint`, LLM, opt-in): per-page summaries (frontmatter + first 500 chars),
  single prompt requesting `---LINT: type | severity | Short title---` blocks; types
  `contradiction, stale, missing-page, suggestion`; severities `warning|info`; stored as
  `type: "semantic"`; **semantic findings are ALWAYS sent to Review**, never auto-fixed.
- Deterministic fixes (`lint-fixes.ts`):
  - `appendWikilink`: add `- [[target]]` under existing `## Related` heading, else append the
    section; idempotent.
  - `rewriteWikilinkTarget`: replace `[[broken]]`/`[[broken|alias]]` with suggested slug,
    alias preserved.
  - `ensureBrokenLinkStub`: create `wiki/queries/<slug>.md`, `type: query`, `tags: [stub, lint]`,
    body "Created by Wiki Lint as a placeholder…".
- Fix routing in the view: orphan → appendWikilink to suggestedSource (else send to Review);
  broken-link → rewrite to suggestion (else create stub, then rewrite); no-outlinks →
  appendWikilink to suggestedTarget; orphans also offer cascade Delete.

## 4. VAULT CREATION / ONBOARDING

- Create dialog (`create-project-dialog.tsx`): **Name** + **AI Output Language** (dropdown,
  `"auto"` filtered OUT at create — user must commit) + **Parent Directory** + **Template**
  (default `general`). On create: Rust `create_project` scaffold → overwrite root `schema.md` /
  `purpose.md` with the template's → create `template.extraDirs` → persist output language.
- Rust scaffold (`src-tauri/src/commands/project.rs:16`): fails if dir exists. Creates
  `raw/sources, raw/assets, wiki/{entities,concepts,sources,queries,comparisons,synthesis}`;
  writes `wiki/index.md` (`# Wiki Index` + empty `## Entities/## Concepts/## Sources/## Queries/
  ## Comparisons/## Synthesis` sections), `wiki/log.md` (`# Research Log\n\n## <today>\n\n-
  Project created`), `wiki/overview.md` (frontmatter type overview + `# Overview`), and
  `.obsidian/{app.json,appearance.json,core-plugins.json}` (attachments → `raw/assets`, ignore
  `.cache`/`.llm-wiki`, graph+backlinks on).
- **Templates** (`src/lib/templates.ts`, 654 lines — port verbatim): ids `research, reading,
  personal, business, general`. Shared blocks: `BASE_SCHEMA_TYPES` table
  (entity/concept/source/query/comparison/synthesis/overview rows), `BASE_NAMING`,
  `BASE_FRONTMATTER` (source pages add authors/year/url/venue), `BASE_INDEX_FORMAT`,
  `BASE_LOG_FORMAT`, `BASE_CROSSREF`, `BASE_CONTRADICTION`.
  - research 🔬 "Deep-dive research with hypothesis tracking and methodology notes";
    extraDirs `wiki/methodology, wiki/findings, wiki/thesis`; +types `thesis` (confidence,
    status), `methodology`, `finding` (source, confidence, replicated); purpose headings:
    Research Question / Hypothesis / Background / Sub-questions / Scope / Methodology /
    Success Criteria / Current Status.
  - reading 📚 "Track a book's characters, themes, plot threads, and chapter notes"; extraDirs
    characters/themes/plot-threads/chapters; +types character (first_appearance, role), theme,
    plot-thread, chapter (chapter, pages); purpose: Book Details / Why I'm Reading / Key Themes /
    Questions Going In / Reading Pace / First Impressions / Final Takeaways.
  - personal 🌱; extraDirs goals/habits/reflections/journal; +types goal (target_date, status,
    progress), habit (frequency, streak, status), reflection (period), journal; purpose: Focus
    Areas / Motivation / Current Goals / Active Habits / Review Cadence / Guiding Principles /
    This Year's Theme.
  - business 💼; extraDirs meetings/decisions/projects/stakeholders; +types meeting, decision
    (ADR-style), project, stakeholder; purpose: Business Context / Objectives / Key Projects /
    Key Stakeholders / Open Decisions / Metrics / Constraints & Risks / Review Cadence.
  - general 📄 "Minimal setup — a blank slate"; no extraDirs; base types only; purpose: Goal /
    Key Questions / Scope / Thesis.

## 5. Cross-cutting

- **Schema routing** (`wiki-schema.ts`): parse root schema.md's `## Page Types` markdown table →
  `{type → dir}` (dir must be `wiki` or start with `wiki/`). `validateWikiPageRouting` errors if
  the page's `type` maps to a different dir, or the dir maps to a different type.
- **Filenames** (`wiki-filename.ts`): `makeQuerySlug` = NFKC, trim, whitespace→`-`, keep
  `\p{L}\p{N}-`, collapse/trim `-`, lowercase, **cap 50 chars**, fallback "query".
  `makeQueryFileName` = `<slug>-<YYYY-MM-DD>-<HHMMSS>.md` (UTC).
- **Source identity** (`source-identity.ts`): path relative to `raw/sources/` (else basename).
  Source summary slug: single-segment → name; multi-segment → `<len>-<readable>--…--<FNV36>`
  capped 120 chars; page at `wiki/sources/<slug>.md`.
- **Frontmatter parsing** (`frontmatter.ts`): strict `^---\n…\n---` first; fallback to the first
  `---…---` whose opener is within the first 6 lines; js-yaml JSON_SCHEMA; one repair pass for
  unbracketed wikilink lists; values normalized to `string | string[]`.
- **Persistence map** (all under `<project>/.llm-wiki/`): ingest-queue.json, ingest-cache.json,
  ingest-warnings.log, image-caption-cache.json, page-history/, review.json, lint.json,
  conversations.json + chats/, chat-preferences.json.
- "Smart retrieval mode" (v0.6.3 commit 3f3cfa0) is a chat/query feature only — irrelevant to
  ingest/review/lint parity.

## 6. Synapse parity notes (decided in the 1.7.0 plan)

- Wikilink density must come from prompt parity; Synapse's `ops/enrich_wikilinks.py` post-pass
  defaults OFF (kept as opt-in extra).
- index.md: Synapse keeps the K3 full catalogue (invariant) and ADDS the code-owned bounded
  `## Recently Updated` section (hybrid; ADR-0078).
- Synapse keeps Postgres/Qdrant indexing, hash gate, watcher (I1) — invisible to output parity.
- Frontmatter `lang` becomes optional in Synapse (llm_wiki has no such field); DB `pages.lang`
  filled by detection.
- Review "Create Page" defaults to the deterministic stub; Synapse's full-LLM generation remains
  as an explicit secondary mode.
