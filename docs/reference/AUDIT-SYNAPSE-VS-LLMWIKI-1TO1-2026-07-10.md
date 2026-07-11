# Synapse vs LLM Wiki — 1:1 Parity Audit (Solution Architect Synthesis)

**Scope:** 9 dimension-deltas from a live prod-vault comparison (`/Volumes/synapse/vault/wiki` vs the nashsu/llm_wiki gold vault). **Corpus reality:** Synapse entities 149 / concepts 527 / sources 158 / queries 133 / synthesis 0 / comparisons 0 (1123 live pages). LLM Wiki entities 202 / concepts 460 / queries 110 / synthesis 4 / comparisons 5 (deduped). This document is the authoritative remediation plan.

---

## 1. Executive summary

Synapse and LLM Wiki diverge in **three structurally-linked ways**, all traceable to a small number of code seams. The headline counts (queries 133-vs-110, synthesis/comparison 0-vs-9, entities 149-vs-202) are symptoms of the same handful of defects.

1. **`wiki/queries/` is 100% garbage, and it poisons everything downstream.** Every one of Synapse's 133 query pages is a lint placeholder — `type: query, tags:[stub,lint]`, named after an *entity/concept* (`alphabet.md`, `amazon-s3.md`, `dora.md`), created because a `[[wikilink]]` had no target. The single culprit is `ops/lint.py:1774-1786 _create_broken_link_stub()`, which hard-codes `type=PageType.QUERY` for every dangling link. LLM Wiki's 110 query pages are *genuine open research questions* ("Azure OpenAI F1 threshold: 85% vs 90%?"). Because query pages are hidden from the graph (`engine.py:122`), these mis-typed ghosts silently **drop the inbound wikilinks that would have anchored real entity hubs**, collapsing the graph backbone. They also inflate the index, break the overview's would-be "Open Questions" section, and get cited as empty `[n]` footnotes in chat.

2. **The same real-world entity mints multiple pages because dedup keys only on exact title-slug.** `write_wiki_page` (`orchestrator.py:1376,1390-1414`) reuses an existing page id ONLY when `_slugify(title)` matches exactly. Since the LLM names the same thing differently per source, **AWS became three pages** (`aws.md` "AWS", `amazon-web-services.md`, `amazon-web-services-aws.md`), plus families like `azure`+`microsoft-azure`, `deloitte`+`deloitte-italia`+`deloitte-italy-it-advisory`. LLM Wiki keeps one canonical page per thing and extracts *finer* granularity (14 `aws-*` entities vs Synapse's 8). Net: Synapse has fewer distinct entities AND splits the ones it has → 149 vs 202.

3. **Synapse copied llm_wiki's ingest-time prohibition on synthesis/comparison but never built the compensating generator.** `_common.py:81-83,105-107` forbid the model from emitting synthesis/comparison during ingest (correct parity), delegating them to "the review queue" — but nothing in bulk import ever resolves a review item, so both counts are permanently **0 vs 4+5**. LLM Wiki authors these as an end-of-import corpus-level pass (their `related:[5]` cross-links prove they were built *after* the corpus existed). These missing pages are also the high-affinity **cross-type bridges** the graph needs; without them 527 concepts ball up with no connective tissue.

Two more process-level (not count-level) divergences round out the picture:

4. **Ingest fragments instead of integrating.** LLM Wiki reads each existing entity/concept page and *rewrites it enriched* (one source touches 10-15 pages, mostly edits). Synapse feeds the model **titles only** (`orchestrator.py:2814-2818`, capped at 400/8000 chars) with a "link, don't duplicate" instruction — it never sees existing page bodies, so it links or invents fresh fragments. This is why concepts over-count (527 vs 460) while entities lag.

5. **Chat citations and file-back are mis-shaped.** Chat cites bare `[n]` indices into a throwaway context block, not page paths; "Save to Wiki" hard-codes `type=query` (`chat.py:664-679`), feeding the query-inflation and synthesis-starvation directly, whereas LLM Wiki files durable answers to `wiki/synthesis/`.

**One correction to the parity program:** the assumed `domain/`/`vendor/`/`tool/` tag-taxonomy work is moot — both corpora already use flat lowercase-kebab tags (0 slash-prefixed on either side). Drop it.

---

## 2. Ranked divergence register

Sorted critical → major → minor → cosmetic. IDs are prefixed by dimension: **CE** classification-entities · **QP** query-pages · **SC** synthesis/comparison · **OV** overview · **IL** index/log · **FW** frontmatter/wikilinks · **LN** lint · **IN** ingest-flow · **CG** chat/graph.

| ID | Dimension | Sev | Divergence | Root cause (code) | Evidence |
|----|-----------|-----|------------|-------------------|----------|
| QP-Q1 | query-pages | **critical** | 133/133 query pages are entity/concept-named lint placeholders, not open questions | `lint.py:1774-1786` hard-codes `type=PageType.QUERY` for every dangling `[[link]]`; `schemas.py:65` maps QUERY→`queries/` | `grep 'Created by Wiki Lint as a placeholder'` = 133 of 133; samples `alphabet.md`, `amazon-s3.md`, `dora.md` |
| QP-Q2 | query-pages | **critical** | Genuine contradiction→open-question generation never fires; contradictions are flag-only no-ops | `lint.py:88 _FLAG_ONLY_CATEGORIES` includes `contradiction`; apply branch only flips status='applied' (~`lint.py:425-434`) | `schema.md:89-91` prescribes the workflow, no code implements it; synthesis=0, comparisons=0 confirm the whole pipeline is inert |
| CE-D1 | classification | **critical** | Same real entity mints multiple pages (dedup keys only on exact title-slug) | `orchestrator.py:1376,1390-1414` reuse id only on identical `_slugify(title)`; no normalization/alias/embedding match | 3 AWS pages; `azure`+`microsoft-azure`, `deloitte`+`deloitte-italia`+`deloitte-italy-it-advisory`, `onetrust`+`onetrust-trust-intelligence-platform` |
| SC-D1 | synthesis/comp | **critical** | No corpus-level pass ever generates synthesis/comparison; prohibition copied, generator never built | `_common.py:105-107,81-83` forbid both at ingest; only counterpart (`review.py:3285`) is human-gated, never fires in bulk import | `synthesis/` & `comparisons/` empty (0 .md) vs 4+5; `_common.py:88-92` comment admits prohibition added for parity but no generator added |
| IL-D2 | index/log | **critical** | Index has no dedup — dumps all 1123 live rows including alias duplicates | `index.py:82-88` emits every live Page row verbatim; no canonicalisation/ghost filtering | `[[AWS]]`+`[[Amazon Web Services]]`+`[[Amazon Web Services (AWS)]]`, `[[Cloudability]]`+`[[Apptio Cloudability]]`; 1123 vs 84 |
| FW-D1 | frontmatter | **critical** | `related:` frontmatter 100% present in LLM Wiki (curated graph seed), entirely absent in Synapse | `schemas.py:102-124` WikiFrontmatter has no `related` field; no code path emits one | LLM 916/916 have `related:` (avg 5.1, 83% resolve); Synapse 0/967 |
| FW-D2 | frontmatter | **critical** | Wikilink targets inverted: LLM links by SLUG, Synapse by TITLE — root of the queries stub flood | Generation emits title-cased targets; `lint.py:1745` then materializes each unresolved title-link as a query stub | LLM 85% slug/4% title; Synapse 9% slug/92% title; `[[DORA Designated Critical ICT Third-Party Service Provider Status]]` |
| IN-D1 | ingest-flow | **critical** | Ingest does NOT integrate facts into existing pages — it links or spawns fragments (concept over-count) | `orchestrator.py:2772-2850` feeds TITLES ONLY + "link, don't duplicate"; generate() never receives existing bodies; only merge seam is slug-collision (`page_merge.py`) | `orchestrator.py:2814-2818`; `_common.py:93-108` has no "read+enrich" step; concepts 527 vs 460, entities 149 vs 202 |
| IN-D2 | ingest-flow | **critical** | Contradiction→query rule is dead text; contradictions degrade to flag-only review items | `_common.py:75-85` restricts suggested_pages to entity\|concept\|source; QUERY provider-forbidden (`schemas.py:39-46`); `vault.py:137-143` rule has no executing code | `lint.py:88 _FLAG_ONLY_CATEGORIES`; synthesis=0, comparisons=0, queries are stubs |
| CG-D1 | chat/graph | **critical** | Chat cites bare `[n]` into an ephemeral context block, not durable page paths/wikilinks | `context.py:41-52 _SYSTEM_PREAMBLE` forces `[n]`, forbids titles/paths in marker; `retrieval.py:677` assembles `[n] <title>` | `context.py:44-49`; done-event citations `{n,id,title,slug}` resolved client-side, never inline page paths |
| CG-D2 | chat/graph | **critical** | "Save to Wiki" files durable answers to `queries/` as `type=query`; LLM Wiki files to `synthesis/` | `chat.py:664-679` hard-codes `PageType.QUERY` + `queries/<slug>.md`; no synthesis file-back path | `chat.py:668,672`; feeds query-inflation (133 vs 110) and synthesis-starvation (0 vs 4) |
| CG-D4 | chat/graph | **critical** | Entity-hub deficit + query-stub inflation collapses graph hub structure | `engine.py:507-509` filters type=query nodes before id_to_idx; `engine.py:518-521` keeps a link only if BOTH endpoints survive → inbound links to stub-hubs silently dropped | entities 149 vs 202 (−26%); stubs `[[Salesforce, Inc.]]`,`[[Oracle Corporation]]`; `GRAPH_HIDDEN_PAGE_TYPES={'query'}` |
| CE-D2 | classification | major | Generation prompt has no canonical-naming policy; model freely varies entity titles | `_common.py:74-84,92-123` say WHAT types to emit, nothing about naming at official/canonical name or reusing an existing title | `_common.py:110-123` schema is `{title,type,content,frontmatter}` only; "AWS"/"Amazon Web Services"/"Amazon Web Services (AWS)" all accepted |
| CE-D3 | classification | major | Existing-pages context is LINK-only and truncated (400/8000), can't converge at scale | `orchestrator.py:2766-2767` caps; framed as wikilink targets only; 1123 live pages | Header `2814-2818` targets linking, not naming/reuse |
| CE-D4 | classification | major | Lower distinct-entity granularity; detail folded into concept prose | `_common.py:74-84,92-108` fully LLM-discretionary; concept is low-friction default | LLM 14 `aws-*` entities vs Synapse 8; concepts higher 527 vs 460 |
| QP-Q3 | query-pages | major | Query frontmatter diverges: Synapse forces `sources:[lint:<uuid>]`+`lang:`; LLM has real sources + question title | F3 non-empty sources applied uniformly → synthetic `lint:` sources | SYN `sources:[lint:edd9c104-…]`; GOLD real raw path + title as question |
| QP-Q4 | query-pages | major | 133 legacy stubs already pollute prod `queries/`, index, and counts — need migration not just fix-forward | Historical bulk-apply via `lint.py:1743`; `engine.py:122` hides them from graph so pollution is invisible there but real in tree/index/counts | queries 133 vs 110; all 133 are stubs |
| SC-D2 | synthesis/comp | major | Synthesis/comparison creation reachable ONLY via human F9 Create; proposals sit pending forever | `review.py:569 _resolve_create_page_type` called only from `_run_generation` (POST create only); no auto-create sweep | `routers/chat.py:274-276` save-to-wiki hard-codes query; `propose_reviews` items stay `pending` |
| OV-D1 | overview | major | Overview has no "Open Questions/Tensioni Irrisolte" block listing `[[query]]` pages | `_build_overview_instruction` (`orchestrator.py:2243-2277`) never asks for it and forbids bulleted lists; no deterministic append | LLM `overview.md:82-108` "19 Query Aperte" (13 resolve); Synapse grep = NONE |
| OV-D2 | overview | major | Overview frontmatter minimal (title+type); LLM has ~129-keyword tag cloud + related/sources/created/updated | Prod predates tag-cloud seam; prompt requests only 20-40 tags, `_OVERVIEW_MAX_TAGS=50`; never emits related/sources/dates | LLM `overview.md:6` 129 tags; Synapse 0 tags |
| IL-D1 | index/log | major | Index entries are bare wikilinks, no em-dash description gloss | `index.py:139,148,155` emit `- [[title]]`; query selects only title/type/path | SYN 0/1123 glossed; LLM 84/84 e.g. `- [[onetrust]] — OneTrust Platform ⭐ UPDATED` |
| IL-D3 | index/log | major | 156-entry `## Uncategorised` section of NULL-type ghost pages; LLM has none | `index.py:99-102,151-156` routes falsy page_type to bucket; upstream unresolved-wikilink ghosts | `## Uncategorised` at `index.md:993`, 156 entries |
| IL-D5 | index/log | major | Log is per-page (1198 machine bullets, verb always `indexed`); LLM is coarse per-document narrative (~21 `Ingest:`) | `orchestrator.py:1506,276` call `append_log` once per `write_wiki_page`; no per-source aggregation | SYN 1198 bullets, `[[BYOL]]`×12; LLM 21 `- Ingest: <Doc> — created N pages` + 7 grouped blocks |
| FW-D3 | frontmatter | major | `sources[]` is a single clean raw pointer in LLM, a noisy 2-entry (label + path) list in Synapse | `orchestrator.py:1450` persists both label and raw path; no dedup | LLM single item; SYN mean 1.95, 685 pages exactly 2, max 13 |
| FW-D4 | frontmatter | major | Frontmatter key ORDER differs: LLM human-ordered (type/title first), Synapse alphabetical | `frontmatter.dumps` (`orchestrator.py:1478-1479`) uses PyYAML `sort_keys=True` | Every SYN page opens `created:` then `lang:`; every LLM page opens `type:` then `title:` |
| LN-D1 | lint | major | Broken-link stub-fix dumps every no-suggestion target into `queries/` as type=query, forced non-empty sources | `lint.py:1745-1826` hard-codes QUERY + flat `queries/`, `sources=[lint:id]`; omits LLM Wiki folder-aware `stubRelativePathFromBrokenTarget` + empty `sources:[]` | `lint.py:1774-1788` vs `lint-fixes.ts:50-104`; LLM queries=110 (0 stubs) vs SYN 133 stubs |
| IN-D3 | ingest-flow | major | Entity identity resolved by title-slug only → name variants duplicate instead of merging | `orchestrator.py:1376,1396-1406` slugs+matches exactly; no canonical/alias resolution pre-slug | `[[AWS]]`+`[[Amazon Web Services]]`+`[[Amazon Web Services (AWS)]]` |
| IN-D4 | ingest-flow | major | No fan-out breadth: a source can yield as little as 1 page vs LLM's 10-15 touched | `_common.py:93-108` gives no page-count expectation; `_ensure_source_summary` guarantees only the source page | `orchestrator.py:1842-1890` |
| CG-D5 | chat/graph | major | Zero synthesis+comparison removes high-affinity cross-type bridges; concept blob has no connective tissue | type_affinity rewards concept↔synthesis=1.2 but penalizes concept↔concept=0.8 (`engine.py:137-140`); 0 bridges + 527 penalized concepts | synthesis 0 vs 4, comparisons 0 vs 5; gold synthesis carry `related:[5]` |
| CG-D3 | chat/graph | major | Saved chat answers are graph-invisible & isolated (type=query + no wikilinks/related) | `chat.py:664-679` saves cleaned text, `sources=['chat']`, no link/generation pass; edges require resolved `[[wikilink]]` | gold `risk-register-cloud-licensing.md` has `related:[5]` — 5 edges Synapse never creates |
| CE-D5 | classification | minor | index.md lists every alias — duplication is user-visible, inflates catalogue | `index.py` regen from live rows, no alias collapse | `index.md:18-22` three AWS entries |
| SC-D3 | synthesis/comp | minor | `propose_reviews` rarely even PROPOSES synthesis/comparison — prompt keys on missing-page/duplicate/contradiction, not comparison/synthesis SHAPE | `review.py:3081-3103` taxonomy, `proposed_page_type` an afterthought | `engine.py:136-140` computes type-affinity/source-overlap signals unused for seeding |
| OV-D3 | overview | minor | No guaranteed bolded thesis / "Nucleo centrale" lead | prompt asks for thesis only in H1 title (`orchestrator.py:2259-2264`) | LLM `overview.md:13` `**Nucleo centrale**: [[…]]` |
| OV-D4 | overview | minor | Even with an Open-Questions block, query pages are ghost stubs — block would be garbage | Upstream query-misfiling defect | queries named `[[Salesforce, Inc.]]` vs `does-scale-improve-reasoning.md` |
| IL-D4 | index/log | minor | Query section header mis-spelled `Querys` | `index.py:161-167 _PLURAL_EXCEPTIONS` omits `query` → `capitalize()+'s'` | `## Querys` at `index.md:857` vs `## Queries` |
| IL-D6 | index/log | minor | Log verb vocabulary only indexed/deleted; no ingest/query/lint operation-level actions | `append_log` action defaults to `indexed`; no coarse summary writer | 1198/1198 `· indexed ·`, 0 ingest/query/lint |
| FW-D5 | frontmatter | minor | Synapse has a required `lang:` key on every page; LLM has none | `schemas.py:116 lang` required (min_length=2) for F3 language-aware ingest | 967/967 SYN vs 0 LLM |
| LN-D2 | lint | minor | Dead `missing-xref` category has no LLM counterpart, never emitted, still in enum+apply router | retained in `_VALID_CATEGORIES` (`lint.py:63-76`) + apply (`442`) but `_parse_findings` drops it (`2107-2110`) | LLM type union has no xref (`lint.ts:10`) |
| LN-D3 | lint | minor | `missing-page` fix runs a full orchestrated generation loop — far heavier than LLM's stub/review | `_apply_missing_page` (`lint.py:1555-1628`) calls `_run_generation` | vs LLM semantic findings (no generative fix) |
| IN-D5 | ingest-flow | minor | synthesis/comparison never populated (ingest prohibited + review path not firing) | `_common.py:104-108` prohibition, no review-driven creation | synthesis 0/comparisons 0 vs 4/5 |
| CG-D6 | chat/graph | minor | Chat retrieval can cite near-empty ghost query-stubs, degrading citation quality | `retrieval.py:820-826` filters only `raw/`; `stream.py:204-210` passes no type_filter | `[3] Salesforce, Inc.` stub citation possible |
| CE-D6 | classification | cosmetic | Entity-vs-concept DEFINITIONS identical both sides — divergence is process-driven, not rule-driven | Both `schema.md` define entity/concept verbatim | same wording both files |
| IL-D7 | index/log | cosmetic | Index preamble is a machine marker; LLM is curated human doc with dated section headings + rich tags | `index.py:48-61,124-129` hardcode auto-gen banner+total+timestamp | SYN "auto-generated…overwritten", "Total pages: 1123" vs LLM `## Sources — … (luglio 2026)` |
| FW-D6 | frontmatter | cosmetic | Parity premise wrong: neither side uses `domain/`/`vendor/`/`tool/` tag prefixes | Assumption in the brief; actual tags flat kebab both sides | `grep` slash-in-tags = 0 both |
| LN-D4 | lint | cosmetic | Category label strings & semantic finding shape differ (breaks 1:1 UI/label parity) | Synapse uses distinct category strings + 4 separate semantic categories, single-target; LLM uses one `semantic` type + `affectedPages[]` | `lint.py:63-76` vs `lint.json` semantic objects |

---

## 3. Alignment plan (phased)

**Ordering rationale:** P0 fixes the correctness defects that everything else depends on — **the query-misclassification fix (P0-1) MUST land before** the overview open-questions block (P1), the index dedup/uncategorised cleanup (P1), and the graph-hub restoration (P0-2), or those features surface garbage. Entity canonicalisation (P0-2) is a prerequisite for index dedup. Synthesis/comparison generation (P0-3) unblocks the graph bridges and the chat file-back.

### P0 — Correctness (must land first)

**P0-1 · Stop mis-filing broken wikilinks as `type=query`; reserve `queries/` for real open questions.**
*Consolidates QP-A1, LN-A1, LN-A2, FW-A3.*
- **Change:** In `_create_broken_link_stub`, replace hard-coded `type=PageType.QUERY` with folder-aware routing mirroring LLM Wiki's `stubRelativePathFromBrokenTarget` — multi-segment target `entities/x` → `entities/x.md`, single-segment → infer ENTITY (proper-noun/Inc./Corp. heuristic) else CONCEPT, never QUERY. **Preferred:** make stub materialisation opt-in and default the no-suggestion path to `send_finding_to_review` (F9), matching LLM Wiki prod reality (0 stub pages in `queries/`). Keep `tags:[stub,lint]` for greppability.
- **Target files:** `backend/app/ops/lint.py:1745-1826` (`_create_broken_link_stub`); `:437-439` (broken-wikilink apply branch); `:489-557` (`send_finding_to_review`); helper mirroring `lint-fixes.ts:50-66`.
- **Effort:** S-M · **Risk:** existing tests assert "under queries/" + `type=query` — update fixtures; F3 requires non-empty `sources` so `sources:[]` on stubs needs a schema exception for stub pages (see escalation Q1); avoid double-enqueue with the existing `broken-wikilink→missing-page` review bridge (`lint.py:477-486`). · **Invariant:** F3 vs I5/K7; K2/K6/K8/F9.

**P0-2 · Entity-identity canonicalisation before slugging + a HITL dedup sweep for the existing vault.**
*Consolidates CE-A1, CE-A2, CE-A3, CE-A4, IN-A3, IL-A2.*
- **Change (fix-forward):** Add `_resolve_canonical_entity` invoked in `write_wiki_page` **before** `_slugify`. For `type=entity`, compute a normalized identity key (casefold; strip parenthetical acronyms `(AWS)`; strip legal suffixes `Inc./Ltd./S.p.A./PRIVATE LIMITED`; fold known acronym↔longform pairs) and match against existing live entity titles by that key; on match, target the EXISTING file (reuse id, union `sources[]`, run `page_merge.mergePageContent`). Deterministic key first; a bge-m3 title-embedding nearest-neighbour is a bounded opt-in second pass routed to the review queue, never a silent fuzzy merge. Inject a canonical-naming rule into ingest prompts + `schema.md` ("name every entity at its canonical short name; reuse an existing entity's EXACT title if it denotes the same thing; never append parenthetical acronyms or legal suffixes"). Give generation a dedicated **untruncated entity-canon index** separate from the 400/8000 link catalogue.
- **Change (retrofit):** New `backend/app/ops/dedup_entities.py` (mirror `ops/reclassify_types.py`): cluster entity pages by the normalized key, propose merges to the F9 review queue (human confirms — Deloitte vs Deloitte Italia are edge cases), on accept union `sources[]`/`related[]`, LLM-merge bodies, repoint inbound `[[wikilinks]]` to canonical, soft-delete aliases, regenerate `index.md`. One `data_version` bump per batch; log `total_cost_usd`.
- **Target files:** `orchestrator.py` (write_wiki_page ~1376-1414; new `_resolve_canonical_entity`, `_load_entity_canon` ~2772-2840); `_common.py` (ANALYZE_SYSTEM/GENERATION_SCAFFOLD); `models.py` (optional `entity_alias` table); new `ops/dedup_entities.py`; endpoint in `main.py`; `/Volumes/synapse/vault/schema.md`.
- **Effort:** L (fix-forward) + XL (retrofit sweep) · **Risk:** over-merging distinct entities — conservative key-only auto-merge, everything fuzzy goes to review; wikilink repointing must not orphan edges; HITL-gated, incremental (I1), never full rescan. · **Invariant:** I1, F13, F9, K3, K6, I7.

**P0-3 · Build the missing corpus-level synthesis/comparison generator (keep ingest prohibition intact).**
*Consolidates SC-A1, SC-A2, SC-A3, IN-A5, CG-A5.*
- **Change:** New `backend/app/ops/synthesize.py`: a bounded post-bulk-import pass (and explicit UI/API action) that (1) picks candidate clusters from graph neighborhoods with high source-overlap ×4 / type-affinity / Adamic-Adar (reuse `graph/engine.py`); (2) `provider.generate` a synthesis body (thesis + integration) and, for ≥2 same-class entities, a comparison table; (3) writes via `write_wiki_page` as `PageType.SYNTHESIS/COMPARISON` with `related[]`=cluster wikilinks, `sources[]`=union of contributing sources. Min-cluster-size + confidence threshold; borderline clusters go to F9 instead of auto-write. Additionally: promote high-confidence synthesis/comparison **review proposals** to auto-create in the bounded sweep, and extend `_llm_propose_reviews` to actively DETECT the comparison/synthesis shape (rule-based seed from graph signals so obvious comparisons are always proposed).
- **Target files:** new `ops/synthesize.py`; `graph/engine.py` (reuse cluster/neighbourhood); `orchestrator.py` (write seam); `routers/ops.py` (endpoint); `ops/review.py` (`sweep_reviews`+`_run_generation` seam; `_llm_propose_reviews` prompt `3081-3103`).
- **Effort:** L · **Risk:** over-production of low-value pages (the exact thing the prohibition guarded) — gate on cluster-size/confidence, route borderline to review, cap `max_iter`+`token_budget`, log cost; keep the single-doc ingest prohibition (`_common.py`) intact — **this new path is a deliberate exception → escalate to solution-architect per the invariant-change rule.** · **Invariant:** F3/K2 (new op), F4 (reuses 4-signal graph), F9, I1 (single write seam), I7.

**P0-4 · Implement genuine contradiction → open-question query generation.**
*Consolidates QP-A2, QP-A4, IN-A2.*
- **Change:** Remove `contradiction` from `_FLAG_ONLY_CATEGORIES` (or add a dedicated human-gated apply branch). On an applied contradiction finding, author a `type=query` page via an internal pipeline writer (not free provider output): title phrased as a question, body `## Question / ## Hypothesis / ## Open Points / ## Impact / ## References`, real `sources[]`=raw docs of both conflicting pages, `related[]`=the two page slugs. Use the InferenceProvider (bounded I7) to phrase question+hypotheses. This unblocks the resolve-in-synthesis step (`schema.md:91`).
- **Target files:** `ops/lint.py:88` + new query writer; `orchestrator.py` (post-write contradiction hook); `ops/review.py` (contradiction path); `schemas.py` (allow query as a *pipeline-written* type via the internal writer, still provider-forbidden).
- **Effort:** L-XL · **Risk:** false-positive contradictions spawn noisy questions — keep human-gated, confidence gate, cap per run; touches the K8 boundary. · **Invariant:** F3/K2/K4, I6/I7, F9, K8.

**P0-5 · Migrate/clean the 133 legacy lint stubs out of `queries/`.**
*Consolidates QP-A3, LN reclassify.*
- **Change:** One-off reviewed batch: select pages where `tags` contains `stub`+`lint` AND `type=query` (greppable by placeholder body). Reclassify to concept/entity stub (move file, rewrite `type`) via the P0-1 folder-aware logic, OR `cascade_delete` if the dangling target is now resolvable/duplicate, then `reresolve_dangling_links`. Dedupe against `index.md` in the same pass. One `data_version` bump per batch.
- **Target files:** migration script under `scripts/`/`ops`; reuse `cascade_delete` + `reresolve_dangling_links`.
- **Effort:** M · **Risk:** cascade_delete must preserve pages a stub was legitimately linked from; file moves must keep `[[wikilinks]]` resolving (I5); do as reviewed batch, not silent. · **Invariant:** I1, I5, F13.

### P1 — Structure (after P0 lands)

**P1-1 · Deterministic "Open Questions / Tensioni Irrisolte" closing block on `overview.md`.** *(OV-A1)*
- Append a `## Open Questions (<period>)` numbered list of `[[title]]` links built from a bounded DB query of live `page_type=='query'` rows (not the LLM), localized via `overview_language`, skipped when zero. **Depends on P0-1/P0-5** — gate on query pages that look like questions or ship after reclassification.
- **Files:** `orchestrator.py` (`_write_and_index_overview ~2372`; new `_build_open_questions_block`; called from `_update_overview ~2065`). **Effort:** M · **Invariant:** F3, K4/F9.

**P1-2 · Index entry descriptions (em-dash gloss) + kill the Uncategorised ghost section + fix `Querys`.** *(IL-A1, IL-A3, IL-A4)*
- Extend index query to select a per-page one-liner (frontmatter description / first-sentence summary / persisted `summary` column populated at generate time — must be **deterministic**, derived from stored data, not a live LLM call, to preserve `update_index` idempotency). Render `- [[title]] — {description}`. Stop rendering `## Uncategorised` (fix NULL `page_type` at source — resolved by P0-1). Add `"query":"Queries"` to `_PLURAL_EXCEPTIONS` with a regression test.
- **Files:** `wiki/index.py` (`82-88`, `99-102`, `138-155`, `151-156`, `161-167`); `models.py` (optional `summary` column). **Effort:** M+S · **Invariant:** K3, I1, K6.

**P1-3 · Coarse per-document ingest log + operation-level verbs.** *(IL-A5, IL-A6)*
- Aggregate created/updated page titles in the orchestrator run context; flush ONE narrative bullet per source at end of run: `## YYYY-MM-DD` / `- Ingest: <Source> — created N pages (X entities, Y concepts, 1 source): [[..]]`. Add coarse verbs for lint (`- Lint: fixed 4 dead wikilinks across 3 pages`) and query/contradiction sweeps.
- **Files:** `orchestrator.py:3054-3120` (`append_log`) + call sites `276,1506`; `ops/lint.py`. **Effort:** L · **Risk:** verify no K4 incremental-refresh consumer parses the per-page log lines before changing granularity; keep cost logging (I7). · **Invariant:** K4, I1, I7.

### P2 — Conventions (parallelisable with P1)

**P2-1 · Add `related: list[str]` to WikiFrontmatter and populate from resolved outbound wikilinks + top graph neighbours (cap ~8, slugs not titles).** *(FW-A1)* — Restores the F4 direct-link seed; **keep `sources`+`lang` untouched** (F3). Additive, `extra=allow` already permits round-trip. **Files:** `schemas.py:102-124`, `orchestrator.py:1449-1477`. **Effort:** M · **Invariant:** F4, I5; F3 untouched.

**P2-2 · Switch wikilink emission to SLUG targets with `[[slug|Title]]` alias.** *(FW-A2)* — Add a title→slug resolver (reuse slugify + Page index), rewrite generated links at write time. This is the deep root fix for the ghost flood — most of the 8087 title-links become resolvable. **Files:** `wiki/links.py`, `ops/enrich_wikilinks.py`, generation prompts, `provider/cli.py:474`. **Effort:** L · **Risk:** collisions; run against a vault copy first. · **Invariant:** K5, I5, F4.

**P2-3 · Canonicalise `sources[]` to a single clean raw pointer; move the human label into the source page's own frontmatter.** *(FW-A4)* — Reduces mean 1.95→~1. **Verify the F13 3-method cascade-delete match still works on canonical paths.** **Files:** `orchestrator.py:1450`, `schemas.py:126-132`. **Effort:** M · **Invariant:** F3, F13.

**P2-4 · Emit frontmatter in human key order (`type,title,created,updated,tags,related,sources,…`) via `sort_keys=False`.** *(FW-A5)* Output-only; improves 1:1 diffability. **Files:** `orchestrator.py:1478-1479`. **Effort:** S · **Invariant:** I5.

**P2-5 · Grow + guarantee the overview tag cloud; add `related:[]`/`sources:[]`/created/updated to overview frontmatter; add a bolded thesis anchor.** *(OV-A2, OV-A3, OV-A4)* — Raise prompt to 40-120 keywords, `_OVERVIEW_MAX_TAGS`→~130; write empty `related:[]`/`sources:[]` YAML keys for the meta page; preserve `created` across regens; require `**Central thesis**:` lead. **Re-run overview regen** so prod (0 tags) gets its cloud. **Files:** `orchestrator.py:2267-2273,2329,2385-2436,2248-2258`. **Effort:** S · **Risk:** overview is a meta page — empty `sources[]` must be explicitly allowed for `type==overview` (see escalation Q1); token budget I7. · **Invariant:** I5, F3, I7.

**P2-6 · Drop the tag-taxonomy work item; correct the parity tracker.** *(FW-A6, LN cleanup)* — Both corpora use flat kebab tags. Also remove the dead `missing-xref` category (LN-D2) accepting it on read, rejecting on write; align lint reporting labels + `affected_pages[]` at the API boundary (LN-D4); make `missing-page` lint fix lightweight (LN-D3) — stub/review instead of full generation loop. **Files:** `docs/reference/SYNAPSE-VS-LLMWIKI-PARITY.md`, `ops/lint.py:63-76,442,1496-1628,145-171,2083-2138`. **Effort:** S-M · **Invariant:** none / I7 (lowers per-fix cost).

### P3 — Larger structural (highest effort, depends on P0)

**P3-1 · Ingest fact-integration: enrich existing pages instead of fragmenting.** *(IN-A1, IN-A4)* — After `analyze()`, resolve `analysis.entities/topics` against existing Page titles (indexed query, I1, matched pages only — no rescan), pass those BODIES as an "ENRICH THESE" block, and change the scaffold from "link, don't duplicate" to "when a subject already exists below, RE-EMIT it enriched (it will be merged), else link or create". Add a fan-out breadth expectation ("a rich source typically yields ~8-15 pages including enriched existing ones; do NOT invent pages the source doesn't support"). **Files:** `orchestrator.py` (`_load_existing_pages_catalogue`/new `_load_matched_page_bodies`), `_common.py` (`build_generate_prompt`+GENERATION_SCAFFOLD), `loop.py`. **Effort:** L · **Risk:** larger prompts (token budget I7); over-rewrite mitigated by the degrade-safe page_merge shrink guard; fetch only matched pages (I1). · **Invariant:** F3/K5, I1, I7.

**P3-2 · Reclassify entity/concept-named query-stubs → entity/concept to rebuild the graph backbone.** *(CG-A4)* — Run the bounded `reclassify_types` sweep over `type=query` pages whose title matches a known entity/concept; un-hiding them restores dropped inbound wikilink edges (`engine.py:518-521`) and hub degree. Targets the −53 entity deficit + the 133→~110 query correction in one pass. **Overlaps P0-5** — sequence together to avoid whack-a-mole. **Files:** `ops/reclassify_types.py`, `POST /ops/reclassify-types`. **Effort:** M · **Risk:** provider cost (I7); mis-classifying a genuine question — strict `schema.md` gating, `force=false`. · **Invariant:** F4, K6, I7.

**P3-3 · Chat file-back → synthesis + linking pass; page-path-style citations; retrieval excludes query stubs.** *(CG-A1, CG-A2, CG-A3, CG-A6)* — Flip `save_chat_to_wiki` `PageType.QUERY→SYNTHESIS` + `wiki/synthesis/` (optional lightweight classifier: genuine follow-up questions still go to queries, answered/analytical content to synthesis). Add a bounded provider link-extraction pass on file-back so saved pages carry `[[wikilinks]]`+`related[]` (edges the engine needs). Expose each context block's slug to the model (`[n] <title> (concepts/<slug>)`) and soften the preamble to allow naming page paths in prose while keeping `[n]` as the resolvable anchor. Pass a default `type_filter` excluding query on the chat retrieval path (setting-gated). **Files:** `chat.py:646-679`, `chat/context.py:41-52`, `rag/retrieval.py:677,820-826`, `chat/stream.py:204-210`, frontend `decorateCitations`. **Effort:** S-M each · **Risk:** endpoint contract change (file_path); new bounded provider call on a previously pure write (I6/I7 — keep no-provider fallback); small local models drifting off the `[n]` grammar. · **Invariant:** F4, F5, F6, I5, I6/I7, I3 (parse at stream end unchanged).

---

## 4. Invariant / decision escalations

These require **solution-architect ADR + owner (Emanuele) sign-off** before the corresponding P-actions land.

**Q1 — F3 sources-traceability vs stub/meta pages with empty `sources[]`.**
LLM Wiki writes lint stubs and the `overview.md` meta page with `sources: []` (empty). Synapse's F3 invariant + `schemas.py:116` require **non-empty** `sources[]`, forcing synthetic `lint:<uuid>` entries (QP-Q3) and blocking clean overview frontmatter (OV-A2/P2-5).
> **Decision needed:** Do we carve a documented F3 exception for `type ∈ {overview, and lint-stub pages}` to permit `sources: []`, or keep a schema-compliant sentinel? This is a genuine invariant amendment — needs an ADR (successor to ADR-0066).

**Q2 — `related:` frontmatter as a first-class field (FW-A1/P2-1).**
LLM Wiki's graph seed is `related:` on 100% of pages; Synapse has none and its 4-signal graph derives edges only from resolved `[[wikilinks]]`. Adding `related:` is additive and Obsidian-valid, but it becomes a **second edge source** feeding F4.
> **Decision needed:** Is `related:` authoritative for graph edges (parity with LLM Wiki), or advisory metadata only, with `[[wikilinks]]` remaining the sole edge source? Affects `graph/engine.py` edge construction and whether `related` values must be validated as live slugs (else they become new ghosts).

**Q3 — New automated writers of `query` and `synthesis`/`comparison` pages (P0-3, P0-4, P3-3).**
`schemas.py:39-46` states QUERY is "never generated by an InferenceProvider" and `_common.py:105-107` prohibits synthesis/comparison at ingest — both deliberate nashsu-parity fixes. P0-3/P0-4 introduce **new pipeline paths** that write these types outside single-doc ingest.
> **Decision needed:** Confirm that a *corpus-level maintenance pass* and a *contradiction handler* are legitimate writers of these types (distinct from the prohibited single-doc ingest), and how strongly they must be HITL-gated vs auto-write above a confidence threshold (K8 boundary). Per the invariant-change rule this MUST be an explicit architect decision, not a silent workaround.

**Q4 — Chat save-to-wiki contract change (P3-3).**
Flipping `PageType.QUERY→SYNTHESIS` changes the `POST /chat/save-to-wiki` response `file_path` and the page type users get. 
> **Decision needed:** Acceptable as a breaking behavioural change (update `SaveToWikiResponse` example + frontend copy), or ship behind a classifier/flag so existing "save creates a query" behaviour is opt-in?

**Q5 — Entity over-merge tolerance (P0-2).**
Deterministic key-merge is safe for `AWS`/`Amazon Web Services (AWS)` but ambiguous for `Deloitte` vs `Deloitte Italia` vs `Deloitte Italy IT Advisory` and `Azure` vs `Microsoft Azure`.
> **Decision needed:** Where is the auto-merge/review boundary? Recommendation: normalized-key exact match auto-merges; anything requiring embedding/fuzzy similarity is a review-queue proposal, never silent. Owner confirms the legal-suffix and acronym fold-lists.

---

## 5. Validation strategy

Parity is proven by **re-running the live ops against a reloaded KB and diffing the prod vault against the gold vault**, not by unit tests alone. A **full KB reload is required** for P0-2/P0-5/P3-2 (they rewrite/merge/delete pages and bump `data_version`); P1/P2 formatting changes can be validated on a targeted regen.

**A. Fixture + unit gates (fast, per-PR):**
- New/updated tests: `test_index_md.py` asserts `## Queries` (not `Querys`) and em-dash glosses present; broken-link stub test asserts the stub lands in `entities/`/`concepts/` (or a review item), never `queries/` with `type=query`; canonical-entity resolver test asserts `AWS`/`Amazon Web Services (AWS)` resolve to one file id; frontmatter serializer test asserts `type` is the first key.
- Run the existing suite green (pytest + the flaky-tolerant SourcesView vitest per the CI gotchas), plus `black` (not `ruff format`) and the mermaid Docs Gate.

**B. Live re-run functions (against a reloaded prod-copy vault):**
1. **Ingest** — re-ingest 3-5 representative multi-source docs (a Cloud Licensing set that historically produced the AWS aliases) through the orchestrated loop. **Assert:** second AWS source enriches the existing `aws.md` (body grows, `sources[]` unions) instead of minting `amazon-web-services.md`; one source touches ~8-15 pages (P3-1); log emits ONE coarse `- Ingest:` bullet.
2. **`ops/dedup_entities`** — run the sweep, confirm review proposals cluster the known families, accept, verify aliases soft-deleted, inbound wikilinks repointed, `index.md` shows one `[[AWS]]` entry, single `data_version` bump, `total_cost_usd` logged.
3. **`ops/reclassify_types`** — run over `type=query`; confirm entity/concept-named stubs migrate and query count drops 133→~110.
4. **Contradiction handler** — feed two docs with a known numeric conflict (an 85% vs 90% threshold analogue); assert a `queries/<question>.md` appears with `## Question/## Hypothesis`, real `sources[]`=both raw docs, `related[]`=both pages.
5. **`ops/synthesize`** — run the corpus pass; assert ≥1 `synthesis/` + ≥1 `comparison/` page written with `related[]` cluster links and unioned `sources[]`.
6. **Overview regen** — assert frontmatter now carries a ≥100-keyword tag cloud + `related:[]`/`sources:[]`/created/updated, a `**Central thesis**` lead, and a closing `## Open Questions` block whose links resolve to real `queries/*.md`.
7. **Chat** — issue a query, save-to-wiki; assert it lands in `synthesis/`, carries `[[wikilinks]]`+`related`, and appears as a connected graph node; verify chat citations expose page slugs and no `[n]` resolves to a stub.
8. **Graph** — trigger FA2 recompute; assert entity nodes are the high-degree hubs, query nodes remain hidden, and synthesis/comparison nodes bridge concept clusters.

**C. Corpus-diff parity metrics (the acceptance scorecard):** After reload, recompute and compare to gold:

| Metric | Synapse now | Gold | Target post-alignment |
|---|---|---|---|
| entities | 149 | 202 | ≥190 (dedup + granularity) |
| concepts | 527 | 460 | ~460-490 (integration reduces fragmentation) |
| queries | 133 stubs | 110 real | ~90-120, 0 with `tags:[stub,lint]` |
| synthesis | 0 | 4 | ≥4 |
| comparisons | 0 | 5 | ≥5 |
| `## Uncategorised` entries | 156 | 0 | 0 |
| index entries with em-dash gloss | 0/1123 | 84/84 | 100% |
| pages with `related:` | 0/967 | 916/916 | ~100% |
| wikilinks resolving to slug | 9% | 85% | ≥80% |
| overview tags | 0 | 129 | ≥100 |

Automate with a `scripts/parity_report.py` that greps both vaults and prints this table (extends the existing `docs/reference/SYNAPSE-VS-LLMWIKI-PARITY.md` tracker).

**D. Screenshots (D5 / Playwright, refreshed for the UI-parity program):** capture (1) the tree panel showing `queries/` populated with question-titled pages; (2) `overview.md` rendered with its tag cloud + Open Questions block; (3) `index.md` with deduped, glossed entries; (4) the graph with entity hubs + synthesis/comparison bridges visible; (5) a chat answer with page-path citations; (6) the lint view showing broken links routed to review rather than creating query ghosts. Diff each against the equivalent LLM Wiki screen per the required live-vs-live parity modus operandi.

**E. Invariant guardrails to assert in CI:** no full-rescan on any of the new ops (I1 — assert single `data_version` bump per batch); every new provider loop logs `total_cost_usd` and honours `max_iter`+`token_budget` (I7); `wiki/` still opens as a valid Obsidian vault after migrations (I5); the ingest-time synthesis/comparison prohibition remains in `_common.py` (only the new corpus-level path may write them — assert via a test that single-doc ingest still rejects those types).