"""
Synapse backend configuration — all values from environment variables.

No secrets, no hardcoded URLs, no hardcoded model IDs or dimensions (I9, AC-DC-5).
All required vars fail fast if missing (pydantic-settings raises on startup).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration read entirely from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str
    """asyncpg DSN, e.g. postgresql+asyncpg://user:pass@host:5432/synapse"""

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_url: str
    """Qdrant HTTP base URL, e.g. http://truenas:6333"""

    qdrant_collection: str = "synapse_pages"
    """Name of the Qdrant collection; override only for test isolation."""

    # ── Embedding (bge-m3 via existing Ollama/service on TrueNAS) ─────────────
    embedding_url: str
    """
    HTTP endpoint for bge-m3 embeddings (I9 — reuse the already-running service).
    Example: http://truenas:11434/api/embeddings
    """

    embedding_dim: int
    """
    Vector dimension of the embedding model (ADR-0004).
    Required — no silent default; the running bge-m3 is the authority.
    Documented default in .env.example: 1024 (bge-m3 standard variant).
    """

    embedding_model: str = "bge-m3"
    """
    Model name to pass in the embedding request body.
    Read from env — never hardcoded (I9 / CLAUDE.md §12).
    """

    embedding_format: str = "ollama"
    """
    Embedding request/response adapter selector (ADR-0031, Feature C).
    Allowed values: "ollama" (default) | "openai".
      - "ollama": POST {"model","prompt"} → parse {"embedding":[...]} (current bge-m3 behavior).
      - "openai": POST {"model","input"} → parse {"data":[{"embedding":[...]}]} for
        OpenAI-compatible /v1/embeddings endpoints (Gemini, hosted gateways, etc.).
    Explicit (not auto-detected) by owner decision — deterministic, fail-fast (I9 / I6-spirit).
    Env var: EMBEDDING_FORMAT.
    """

    embedding_api_key: str | None = None
    """
    SECRET. Optional bearer token for the embedding endpoint (ADR-0031, Feature C).
    When set, every embedding request carries `Authorization: Bearer <key>` (both formats).
    When unset (local bge-m3 on the internal network), no auth header is sent (unchanged).
    Never logged, never returned by GET /config/embedding — env-sourced only.
    Env var: EMBEDDING_API_KEY.
    """

    embeddings_enabled: bool = True
    """
    Global toggle for the embedding data plane (ADR-0030, Feature B).
    When True (default): ingest vectorizes pages into Qdrant; retrieval uses Phase-1 dense
    Qdrant search (bge-m3). When False: ingest skips Qdrant; startup skips
    ``_validate_embedding_and_collection``; retrieval Phase 1 degrades to a bounded
    Postgres keyword/title search (``_phase1_lexical_search``). Phases 2–4 (graph-expansion,
    budget, assembly) are UNCHANGED in both modes. Toggling does NOT trigger a re-scan or
    bulk re-embed (I1). Env var: EMBEDDINGS_ENABLED.
    """

    embed_max_chars: int = 4_000
    """
    First-pass cap on CHARACTERS sent to the embedding endpoint in ONE request (I7 — oversize
    guard). Text longer than this is truncated (a WARNING is logged) before embedding, so the
    vector represents the document's head rather than failing.

    NOTE: this is a character cap, but bge-m3's real limit is ~8192 TOKENS. Token-dense content
    (Marker-extracted tables, numeric registries, legal refs) packs >1 token/char, so even a few
    thousand chars can exceed 8192 tokens — live-proven: ~4 000 dense chars 500 while ~3 000 pass.
    A char cap therefore CANNOT guarantee the request fits. The real guarantee is in
    ``EmbeddingClient.embed``: it catches the "input length exceeds the context length" 500 and
    retries with the input halved down to a floor (bounded). If it still 500s at the floor,
    ``upsert_vector`` (ingest/orchestrator.py) catches EmbeddingError and persists a vector-less
    page — the page stays indexed in Postgres/wikilinks/log, only the Qdrant vector is skipped, so
    one dense page never aborts the whole document. This cap just keeps the common case in one
    round-trip. Env var: EMBED_MAX_CHARS.
    """

    overview_language: str | None = None
    """
    Force the OVERVIEW note's language (F3), overriding auto-detection. ISO code, e.g. "it".
    By default (None) the overview matches the vault's content language (the just-ingested
    source's detected language, else the modal `lang` of recent pages). That means an
    English-content vault (e.g. imported ServiceNow docs) yields an English overview. Set this
    when you want the overview narrative in a FIXED language regardless of content — e.g. an
    Italian user reading English source material. Env var: OVERVIEW_LANGUAGE.
    """

    # ── Vault ─────────────────────────────────────────────────────────────────
    vault_id: str = "default"
    """Logical vault identifier (one vault in v0.1; supports multi-vault later)."""

    vault_path: str = "vault"
    """
    Absolute or repo-relative path to the vault root directory.
    The watcher watches <vault_path>/raw/sources/.
    """

    # ── Frontend / CORS ─────────────────────────────────────────────────────────
    cors_allow_origins: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "tauri://localhost,"
        "http://tauri.localhost"
    )
    """
    Comma-separated list of browser origins allowed to call the API (CORS).

    Default covers:
      - http://localhost:5173      Vite dev server (all platforms)
      - http://127.0.0.1:5173     Vite dev server (explicit loopback)
      - tauri://localhost          Tauri v2 packaged webview on macOS/Linux (WebKit)
      - http://tauri.localhost     Tauri v2 packaged webview on Windows (WebView2)

    IMPORTANT — credentials constraint (ADR-0047 §2.4 / risk 3):
    The CORS middleware runs with ``allow_credentials=True``. Under the CORS spec this
    FORBIDS the ``*`` wildcard — a ``*`` default would silently break all credentialed
    preflight requests. Origins MUST always be listed explicitly. Override via
    CORS_ALLOW_ORIGINS env var in production (tunnel, Tailscale, etc.), but NEVER use
    a bare ``*``.

    Env var: CORS_ALLOW_ORIGINS.
    """

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS allow-origins as a list (split + trimmed)."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def vault_root(self) -> Path:
        """Resolved absolute Path to the vault root."""
        return Path(self.vault_path).resolve()

    @property
    def raw_sources_dir(self) -> Path:
        """vault/raw/sources/ — the watched directory."""
        return self.vault_root / "raw" / "sources"

    @property
    def wiki_dir(self) -> Path:
        """vault/wiki/ — the Obsidian-compatible wiki output dir."""
        return self.vault_root / "wiki"

    @property
    def log_md_path(self) -> Path:
        """vault/wiki/log.md — append-only ingest history (K4)."""
        return self.wiki_dir / "log.md"

    # ── Deep Research (F10, ADR-0024) ────────────────────────────────────────────
    searxng_url: str | None = None
    """
    HTTP base URL for the SearXNG search backend (R8 — already running on TrueNAS).
    Example: http://searxng:8080
    Required when deep research is used; POST /research/start returns 503 if unset (I9).
    No API key — SearXNG is open-access on the internal network.
    NEVER falls back to another search engine when unset (I9 / Do-NOT #3).
    """

    # ── P3-e: pluggable web-search provider (ADR-0066/ADR-0070, opt-in, off by default) ──
    # SearXNG stays the DEFAULT, bundled, privacy-preserving backend. The alternatives below are
    # OPT-IN, OFF by default (ADR-0066 amends I9). The NON-SECRET selector `web_search_provider`
    # is a runtime config-override key (S23, config_overrides.py); the four cloud API keys are
    # SECRET and env-only — they are NEVER added to the config-override surface (§2.4), exactly
    # like MINERU_API_KEY (ADR-0069).

    web_search_provider: str = "searxng"
    """
    Env baseline for the active web-search backend (ADR-0070, P3-e). One of:
    searxng | tavily | serpapi | firecrawl | brave | ollama_web. Default "searxng" (the bundled,
    privacy-preserving backend). Runtime-overridable via PUT /config/app/web_search_provider (S23).
    The dispatcher reads the EFFECTIVE value (override-else-env) — never hardcoded (I6).
    Env var: WEB_SEARCH_PROVIDER.
    """

    tavily_api_key: str = ""
    """
    SECRET. Tavily cloud search API key (ADR-0070). Env-only; NEVER exposed via the config-override
    surface (§2.4). Empty → the Tavily backend is a no-op that returns [] (opt-in: nothing is sent
    until this is set). ⚠️ CLOUD (I9): when set AND selected, queries leave the local network.
    Env var: TAVILY_API_KEY.
    """

    serpapi_api_key: str = ""
    """
    SECRET. SerpApi cloud search API key (ADR-0070). Env-only; NEVER on the config-override surface
    (§2.4). Empty → the SerpApi backend is a no-op ([]). ⚠️ CLOUD (I9). Env var: SERPAPI_API_KEY.
    """

    firecrawl_api_key: str = ""
    """
    SECRET. Firecrawl cloud search API key (ADR-0070). Env-only; NEVER on the config-override
    surface (§2.4). Empty → the Firecrawl backend is a no-op ([]). ⚠️ CLOUD (I9).
    Env var: FIRECRAWL_API_KEY.
    """

    brave_api_key: str = ""
    """
    SECRET. Brave Search API subscription token (ADR-0070). Env-only; NEVER on the config-override
    surface (§2.4). Empty → the Brave backend is a no-op ([]). ⚠️ CLOUD (I9).
    Env var: BRAVE_API_KEY.
    """

    deep_research_max_iter: int = 3
    """
    Default max iterations for run_deep_research (ADR-0024 §3.1).
    Caller-overridable via POST /research/start body (bounded 1..10).
    Env var: DEEP_RESEARCH_MAX_ITER.
    """

    deep_research_token_budget: int = 100_000
    """
    Default token budget for run_deep_research (ADR-0024 §3.1).
    Caller-overridable via POST /research/start body (bounded 1_000..1_000_000).
    Env var: DEEP_RESEARCH_TOKEN_BUDGET.
    """

    deep_research_max_queries: int = 5
    """
    Maximum SearXNG queries generated per iteration (ADR-0024 §3.1).
    Env var: DEEP_RESEARCH_MAX_QUERIES.
    """

    deep_research_fetch_max_chars: int = 20_000
    """
    Per-source content cap (chars) after HTML→markdown extraction (ADR-0024 §4).
    Prevents a single large page from blowing the token budget.
    Env var: DEEP_RESEARCH_FETCH_MAX_CHARS.
    """

    deep_research_optimize_timeout_seconds: float = 30.0
    """
    Timeout (seconds) for the single pre-run topic-optimization provider call
    (B5/D3, F10). One bounded provider.chat() turn — no loop (I7). On timeout the
    endpoint degrades to the naive {optimized_topic: topic, queries: [topic]} fallback
    (never 500). Env var: DEEP_RESEARCH_OPTIMIZE_TIMEOUT_SECONDS.
    """

    deep_research_provider_timeout_seconds: float = 120.0
    """
    Per-call timeout (seconds) for the three in-loop deep-research provider turns
    (query generation, sufficiency assessment, synthesis). Enforces the I7 per-call
    time ceiling so a hung provider can never wedge a run at status='running' forever;
    on timeout each step degrades gracefully (empty result / insufficient) and the
    bounded loop proceeds or exits. Env var: DEEP_RESEARCH_PROVIDER_TIMEOUT_SECONDS.
    """

    deep_research_optimize_token_budget: int = 2_000
    """
    Token budget for the single topic-optimization call (B5/D3, F10, I7). Small: the
    overview/purpose excerpt + a rephrased topic + 3-5 queries fits comfortably in 2K.
    Surfaced as a provider hint only; the hard bound is the single call + wait_for timeout.
    Env var: DEEP_RESEARCH_OPTIMIZE_TOKEN_BUDGET.
    """

    # ── F9: Review queue (ADR-0025 §3.2) ────────────────────────────────────────

    review_query_timeout_seconds: float = 30.0
    """
    Timeout in seconds for the single pre-generated-query provider call (F9, I7).
    On timeout → pre_generated_query=NULL; item still enqueued (AC-F9-4).
    Env var: REVIEW_QUERY_TIMEOUT_SECONDS.
    """

    review_query_token_budget: int = 2_000
    """
    Token budget for the single query-gen call (F9, I7).
    Small: the prompt + 1-3 questions should comfortably fit in 2K tokens.
    Env var: REVIEW_QUERY_TOKEN_BUDGET.
    """

    # ── F9 redesign: proposal model bounds (ADR-0034 §4/§6, [AI] §11.2) ──────────
    # All three new LLM call sites (proposal emission, sweep Pass-2, on-demand Create)
    # are bounded by these + the resolved provider row's max_iter/token_budget (I7).

    review_propose_min_chars: int = 10_000
    """
    Anti-spam gate (ADR-0034 §4.2): the proposal LLM call runs only if the total written
    content is at least this many characters (one of several OR'd gate conditions). Below the
    gate (and absent any dangling-link / not-written-suggested signal) → zero proposals, zero
    cost. Env var: REVIEW_PROPOSE_MIN_CHARS.
    """

    review_propose_min_pages: int = 1
    """
    Anti-spam gate (ADR-0034 §4.2): the proposal LLM call runs if at least this many pages were
    written in the run (OR'd with the char / dangling-link / suggested-page conditions).
    v1.5.2: lowered 4 → 1 so the curated LLM review step runs on ordinary single-page ingests too
    (nashsu/llm_wiki flags reviews inside generation and never skips; this keeps parity on volume).
    Env var: REVIEW_PROPOSE_MIN_PAGES.
    """

    review_rule_propose_max_items: int = 8
    """
    Hard cap on deterministic dangling-link / not-written-suggested proposals per ingest run.
    Kept separate from the AI cap so rule noise can never starve high-signal AI proposals.
    Env var: REVIEW_RULE_PROPOSE_MAX_ITEMS.
    """

    review_propose_max_items: int = 12
    """
    Hard cap on AI proposals emitted per ingest run (ADR-0034 §4.3, Do-NOT #9). Combined with
    review_rule_propose_max_items=8, the default total is bounded to 20 while reserving 12 slots
    for source-grounded AI proposals. Env var: REVIEW_PROPOSE_MAX_ITEMS.
    """

    review_propose_token_budget: int = 4_000
    """
    Fallback token budget for the single proposal call (ADR-0034 §4.3, I7) when the resolved
    provider row carries none. Small: a compact analysis digest + ≤8 proposals fits comfortably.
    Env var: REVIEW_PROPOSE_TOKEN_BUDGET.
    """

    review_propose_source_chars: int = 6_000
    """
    Max characters of the RAW source text fed into the proposal prompt (llm_wiki
    buildReviewSuggestionPrompt parity). llm_wiki feeds the model the source *content* — not just
    the analysis — which is what lets it quote the document ("the doc excludes X as out-of-scope")
    and spot in-scope/out-of-scope handoff gaps, producing source-grounded suggestions instead of
    generic "missing from vault" ones. Trimmed head+tail so a scope/exclusions section near either
    end is captured. Bounded to keep the single call (I7) cost-capped; 0 disables the excerpt.
    Env var: REVIEW_PROPOSE_SOURCE_CHARS.
    """

    review_propose_written_pages_chars: int = 6_000
    """
    Total character budget for excerpts from pages written by the current ingest run. Delegated
    review loads only its captured page IDs, then this cap bounds their on-disk excerpts; no vault
    scan is performed. Set 0 for title/type-only digests. Env var:
    REVIEW_PROPOSE_WRITTEN_PAGES_CHARS.
    """

    review_propose_timeout_seconds: float = 120.0
    """
    Timeout (seconds) wrapping the single proposal provider call (ADR-0034 §4.3, I7).
    On timeout → emit only the rule-based proposals (degrade, never fail ingest).
    Sized for the CLI provider: a single ``complete()`` spawns a `claude` subprocess whose
    cold-start + generation routinely exceeds 30s; fast API/Ollama providers return well under
    this ceiling, so the larger default only affects a genuinely-slow/hung call (degrade-safe).
    Env var: REVIEW_PROPOSE_TIMEOUT_SECONDS.
    """

    # ── F18 corpus synthesis bounds (ADR-0074) ────────────────────────────────

    synthesize_max_pages: int = 12
    """Auto-written corpus pages per run before the independent hard cap (I7)."""

    synthesize_max_candidates: int = 40
    """All corpus clusters evaluated per run, including Review/skip paths (I7)."""

    synthesize_token_budget: int = 60_000
    """Aggregate provider-token budget for one explicit corpus run (I7)."""

    synthesize_auto_confidence: float = 0.6
    """Minimum deterministic cluster confidence for automatic page generation."""

    synthesize_review_floor: float = 0.35
    """Minimum confidence for routing a corpus candidate to human Review."""

    # ── I1-parity: nashsu/llm_wiki ingest-quality features (ADR-0063) ────────────────
    # Three orchestrated-route ingest capabilities ported from nashsu/llm_wiki. All three
    # apply ONLY to the orchestrated (Local / API) route — the delegated/CLI route runs the
    # agent's own loop and is a documented gap (ADR-0063 §7, mirroring ADR-0037 §7). Every knob
    # routes its LLM work through the InferenceProvider abstraction (analyze / chat seams — I6),
    # is bounded (max_iter / max_chunks / single-call+timeout — I7), and degrades safely.

    ingest_long_source_char_threshold: int = 48_000
    """
    Feature 1 (ADR-0063 §3) — long-source chunked analysis trigger. When a source's text
    exceeds this many characters, ``analyze()`` is run per bounded chunk and the resulting
    Analysis objects are merged (union topics/entities/suggested_pages, concatenate summaries)
    instead of sending the whole source in one ``analyze()`` call. At/under the threshold the
    normal single-call path runs unchanged. Set to 0 to DISABLE chunking entirely (always
    single-call). Routes every chunk through ``provider.analyze()`` (I6). Bounded by
    ``ingest_long_source_max_chunks`` (I7). Env var: INGEST_LONG_SOURCE_CHAR_THRESHOLD.
    """

    ingest_long_source_chunk_chars: int = 24_000
    """
    Feature 1 (ADR-0063 §3) — target size (characters) of each semantic chunk in the long-source
    analysis path. Chunks split on paragraph boundaries and pack greedily up to this size, with a
    small overlap. Clamped to a 4k floor so a tiny value cannot explode the chunk count.
    Env var: INGEST_LONG_SOURCE_CHUNK_CHARS.
    """

    ingest_long_source_max_chunks: int = 8
    """
    Feature 1 (ADR-0063 §3, I7) — HARD cap on the number of analyze() calls the long-source path
    makes for one source. If splitting yields more chunks than this, only the first N are analyzed
    and merged (bounded cost — never one analyze() call per paragraph of a huge document).
    Env var: INGEST_LONG_SOURCE_MAX_CHUNKS.
    """

    ingest_long_source_checkpoint_enabled: bool = True
    """
    Feature 1 (ADR-0063 §3) — persist a best-effort on-disk checkpoint of completed per-chunk
    analyses under ``vault_root/.synapse/ingest-progress/`` keyed by source hash, so a mid-way
    failure (or a retry) resumes from the last completed chunk instead of re-analyzing from
    scratch. All checkpoint I/O is swallowed on error — it NEVER blocks or fails ingest. Set
    false to keep only the in-run accumulation. Env var: INGEST_LONG_SOURCE_CHECKPOINT_ENABLED.
    """

    ingest_reingest_merge_enabled: bool = True
    """
    Feature 2 (ADR-0063 §4) — LLM body-merge on re-ingest. When a generated page targets a
    ``(vault_id, file_path)`` that ALREADY exists with meaningful prior body content, the writer
    asks the provider (``chat()`` seam — I6) to merge the old + new bodies into one coherent body
    rather than overwriting. Bounded to a single provider call wrapped by
    ``ingest_reingest_merge_timeout_seconds`` (I7); on failure/timeout/sanity-reject it degrades
    to the existing new-body-overwrite behavior. Set false to always overwrite (pre-parity
    behavior). Env var: INGEST_REINGEST_MERGE_ENABLED.
    """

    ingest_reingest_merge_timeout_seconds: float = 60.0
    """
    Feature 2 (ADR-0063 §4, I7) — timeout (seconds) wrapping the single body-merge provider call.
    On timeout the merge is abandoned and the writer keeps the new body (degrade-safe).
    Env var: INGEST_REINGEST_MERGE_TIMEOUT_SECONDS.
    """

    ingest_language_guard_enabled: bool = True
    """
    Feature 3 (ADR-0063 §5) — wrong-language page drop. After ``generate()``, each produced page
    whose detected body script-family does NOT match the resolved target output language
    (``Analysis.language``) is DROPPED (logged) before validate/write. Deterministic, script-based
    detection (no provider call). Exempt: index/overview/log (never in the generated batch) plus
    ``source`` and ``entity`` pages (they legitimately cite cross-language proper nouns — matches
    nashsu/llm_wiki). Only cross-script mismatches drop; intra-Latin differences never do (avoids
    false drops). Set false to disable the guard. Env var: INGEST_LANGUAGE_GUARD_ENABLED.
    """

    ingest_generation_source_char_budget: int = 24_000
    """
    D1 (ADR-0063 §9, nashsu/llm_wiki parity — ingest.ts:926-945/1000-1016) — max characters of the
    ORIGINAL source document threaded into ``generate()`` alongside the Analysis. llm_wiki passes
    analysis + the (budget-trimmed) full source to generation so pages are written from the source,
    not only the lossy Analysis summary. Synapse mirrors this: ``build_generate_prompt`` emits a
    ``# Source document`` section trimmed to this many characters (I7 — never blow the context
    window). Set to 0 to DISABLE threading the source into generation (Analysis-only, the pre-D1
    behaviour). Env var: INGEST_GENERATION_SOURCE_CHAR_BUDGET.
    """

    # ── ADR-0076: block-based orchestrated ingest (nashsu/llm_wiki v0.6.3 parity) ─
    # The 1.7.0 block pipeline is a faithful port of llm_wiki's markdown-analysis +
    # FILE/REVIEW-block generation contract. As of 1.7.0 it is the DEFAULT ("blocks"); the
    # legacy JSON loop (loop.py) remains reachable via ``ingest_pipeline_format="json"`` as a
    # pure rollback lever (slated for removal in 1.8). The 1:1 E2E vs llm_wiki confirmed the
    # block path reaches 12/12 parity bands where the JSON/delegated path dangled wikilinks.

    ingest_pipeline_format: str = "blocks"
    """
    Orchestrated-ingest pipeline selector (ADR-0076) — the 1.7.0 rollback lever. One of:

      • "blocks" — the nashsu/llm_wiki v0.6.3 block path (``block_loop.run_block_loop``):
                   free-markdown analysis → FILE/REVIEW-block generation → block-specific
                   validation → augment & retry, written via ``block_writer.write_block_page``
                   (custom page types persist as the raw ``pages.type`` string). DEFAULT.
      • "json"   — the legacy two-step JSON loop (``loop.run_orchestrated_loop``): analyze →
                   generate (JSON WikiPage list) → validate → augment & retry. Rollback only.

    In "blocks" mode ALL providers — Local, API, AND the agentic CLI — run the block loop via
    ``provider.complete()`` (llm_wiki drives its CLI as a TEXT transport, not an agent loop). The
    delegated/CLI agent loop is used ONLY in "json" mode, where its wikilinks can dangle because
    the agent does not materialise every page it links (the exact gap the 1:1 E2E surfaced).
    Read via ``config_overrides.effective_str`` (override-else-env). Env var:
    INGEST_PIPELINE_FORMAT.
    """

    ingest_context_char_budget: int = 204_800
    """
    Block pipeline (ADR-0076) — total context budget in CHARACTERS (llm_wiki
    ``context-budget.ts`` default maxContextSize 204800). Governs the generation ``max_tokens``
    tier (8192 <128K, 16384 ≥128K, 24576 ≥256K, 32768 ≥512K chars) and the review-stage prompt's
    internal section/index caps. Larger windows earn a higher generation ceiling; smaller ones
    stay bounded (I7). Only consulted when ``ingest_pipeline_format`` == "blocks".
    Env var: INGEST_CONTEXT_CHAR_BUDGET.
    """

    ingest_review_stage_min_chars: int = 10_000
    """
    Block pipeline (ADR-0076, llm_wiki ``shouldRunDedicatedReviewStage`` ingest.ts:2036) — the
    dedicated review stage runs when the generation text is at least this many characters (OR when
    the FILE-block count reaches ``ingest_review_stage_min_file_blocks``). Below both thresholds no
    extra review call is made (I7 — cost control); inline ``---REVIEW:`` blocks in the generation
    are still collected. Env var: INGEST_REVIEW_STAGE_MIN_CHARS.
    """

    ingest_review_stage_min_file_blocks: int = 4
    """
    Block pipeline (ADR-0076, llm_wiki ``shouldRunDedicatedReviewStage`` ingest.ts:2036) — the
    dedicated review stage runs when the generation produced at least this many FILE blocks (OR the
    generation text clears ``ingest_review_stage_min_chars``). Env var:
    INGEST_REVIEW_STAGE_MIN_FILE_BLOCKS.
    """

    ingest_page_history_max_per_page: int = 20
    """
    Block pipeline (ADR-0076, llm_wiki ``.llm-wiki/page-history`` parity) — max on-disk backups
    kept per page under ``<vault>/.synapse/page-history/`` before an overwrite. When
    ``block_writer.write_block_page`` is about to overwrite an existing wiki file it first copies
    the prior bytes to a deterministically-indexed backup; older backups beyond this cap are pruned
    (oldest first). Bounds disk growth (I7). Env var: INGEST_PAGE_HISTORY_MAX_PER_PAGE.
    """

    review_sweep_max_items: int = 200
    """
    Max pending missing-page/duplicate items processed by the sweep Pass-1 rule pass per run
    (ADR-0034 §6.2, I7 — bounded indexed read, no vault re-scan).
    Env var: REVIEW_SWEEP_MAX_ITEMS.
    """

    # ── F3: auto-maintained Overview (nashsu/llm_wiki parity) ────────────────────
    # The single overview.md note is REGENERATED (full overwrite) on every ingest via ONE
    # bounded provider call (I6/I7), then indexed as a Page(type=overview). Degrade-safe:
    # on any failure the previous overview.md is kept and ingest still succeeds.

    overview_title: str = "Overview"
    """
    Frontmatter title for the auto-maintained overview.md note (F3, I5). The wiki's big-picture
    page; shown under the nav "Overview" section (count 1). Env var: OVERVIEW_TITLE.
    """

    overview_max_titles: int = 200
    """
    Max existing page titles+types fed into the overview regeneration prompt (bounded indexed
    read — I1, no vault re-scan). Env var: OVERVIEW_MAX_TITLES.
    """

    overview_token_budget: int = 3_000
    """
    Fallback token budget for the single overview regeneration call (I7) when the resolved
    provider row carries none. Small: the overview is a concise narrative. Env var:
    OVERVIEW_TOKEN_BUDGET.
    """

    overview_timeout_seconds: float = 120.0
    """
    Timeout (seconds) wrapping the single overview regeneration provider call (F3, I7). On
    timeout the previous overview.md is kept (degrade, never fail ingest). The old 30 s default
    was too tight for the CLI (claude-agent-sdk) route on a large vault (agent startup + a
    200-title structured prompt), so every regen timed out and the overview stayed stale. 120 s
    (aligned with the review sweep/propose ceilings) gives the CLI's single ``complete()``
    subprocess room to finish. Env var: OVERVIEW_TIMEOUT_SECONDS.
    """

    review_sweep_llm_enabled: bool = True
    """
    Gate for the sweep Pass-2 conservative LLM judgment (ADR-0034 §6.3). Default on (a single
    bounded call). Set false for zero-cost operation: Pass-1 still runs; Pass-2 returns set()
    (keep all pending). Env var: REVIEW_SWEEP_LLM_ENABLED.
    """

    review_sweep_llm_max_items: int = 40
    """
    Items per sweep Pass-2 LLM judge call — the BATCH size (nashsu/llm_wiki JUDGE_BATCH_SIZE=40).
    The sweep processes up to review_sweep_llm_max_batches batches, so the effective ceiling is
    review_sweep_llm_max_items × review_sweep_llm_max_batches items per run (I7-bounded).
    Env var: REVIEW_SWEEP_LLM_MAX_ITEMS.
    """

    review_sweep_llm_max_batches: int = 5
    """
    Max sweep Pass-2 LLM judge calls per run (nashsu/llm_wiki MAX_JUDGE_BATCHES=5). Bounds the
    sweep at review_sweep_llm_max_items × this many items (I7).
    Env var: REVIEW_SWEEP_LLM_MAX_BATCHES.
    """

    review_sweep_llm_token_budget: int = 4_000
    """
    Fallback token budget for the single sweep Pass-2 call (ADR-0034 §6.3, I7) when the resolved
    provider row carries none. Env var: REVIEW_SWEEP_LLM_TOKEN_BUDGET.
    """

    review_sweep_timeout_seconds: float = 120.0
    """
    Timeout (seconds) wrapping the single sweep Pass-2 provider call (ADR-0034 §6.3, I7).
    On timeout / ambiguity → keep ALL pending (default-to-keep, Do-NOT #7).
    Sized for the CLI provider: a single ``complete()`` spawns a `claude` subprocess whose
    cold-start + generation routinely exceeds 30s; fast API/Ollama providers return well under
    this ceiling, so the larger default only affects a genuinely-slow/hung call (degrade-safe).
    Env var: REVIEW_SWEEP_TIMEOUT_SECONDS.
    """

    # ── ADR-0044 (F9 depth pass) — contextual depth + bulk bounds ────────────────

    review_referenced_pages_max: int = 8
    """
    Cap on referenced existing pages carried per proposal (ADR-0044 §2/§4.3). The proposal
    call's `referenced_page_titles` list is truncated to this at parse; resolution drops
    non-resolving titles. Bounds referenced_page_ids to this length (I7).
    Env var: REVIEW_REFERENCED_PAGES_MAX.
    """

    review_search_queries_max: int = 3
    """
    Cap on pre-generated search queries carried per proposal (ADR-0044 §2.3/§4.3). The proposal
    call's `search_queries` list is truncated to this at parse. search_queries[0] seeds Deep
    Research. Rides the SAME single proposal call (no extra provider call, I6/I7).
    Env var: REVIEW_SEARCH_QUERIES_MAX.
    """

    review_bulk_max_ids: int = 200
    """
    Cap on the number of ids in a POST /review/queue/bulk request (ADR-0044 §6, I7). Over the
    cap → HTTP 400 (never an unbounded bulk write). DELETE /review/queue/resolved is one bounded
    vault-scoped statement (no id list). Env var: REVIEW_BULK_MAX_IDS.
    """

    # ── F2: purpose.md drift suggestions (R9-3, v0.9) ────────────────────────────
    # After each ingest run, a single bounded provider call compares the run's analysis
    # topics/summary against the vault purpose.md. If it detects scope drift (a recurring
    # theme not covered by purpose), it emits ONE `purpose-suggestion` ReviewItem. The call
    # is bounded (max_tokens 300, single call, no retry) and NEVER breaks ingest (I7).

    purpose_suggestion_enabled: bool = True
    """
    Master gate for the post-ingest purpose.md drift check (R9-3). Default on. Set false for
    zero-cost ingest: no drift call is made. Env var: PURPOSE_SUGGESTION_ENABLED.
    """

    purpose_suggestion_max_tokens: int = 300
    """
    Hard output cap on the single bounded drift-detection provider call (R9-3, AC READ:
    "bounded provider call max_tokens 300, no retry"). Env var: PURPOSE_SUGGESTION_MAX_TOKENS.
    """

    purpose_suggestion_min_sources: int = 3
    """
    Throttle N (R9-3): the drift check fires only when at least this many `source` pages have
    been ingested since the newest existing purpose-suggestion ReviewItem (cheap counter — a
    bounded indexed COUNT over pages.created_at, no new column, no migration). Below N → skip
    the provider call entirely (zero cost). Env var: PURPOSE_SUGGESTION_MIN_SOURCES.
    """

    purpose_suggestion_timeout_seconds: float = 20.0
    """
    Timeout wrapping the single drift-detection provider call (R9-3, I7). On timeout / any
    failure → no ReviewItem, ingest still completes. Env var: PURPOSE_SUGGESTION_TIMEOUT_SECONDS.
    """

    # ── K6: schema.md co-evolution (R9-4, v0.9) ──────────────────────────────────
    # After each ingest run (right after the R9-3 purpose check), a single bounded provider
    # call compares the ingested pages' actual frontmatter/type/tag usage patterns against the
    # vault schema.md rules. If it detects a recurring convention that is NOT yet codified (a
    # tag family, a consistently-used frontmatter field, a type misfit), it emits ONE
    # `schema-suggestion` ReviewItem. The call is bounded (max_tokens 400, single call, no
    # retry) and NEVER breaks ingest (I7). Mirrors R9-3 exactly, with two deliberate deltas:
    # (1) DEFAULT OFF and (2) a higher default min-sources — see below.

    schema_suggestion_enabled: bool = False
    """
    Master gate for the post-ingest schema.md co-evolution check (R9-4). DEFAULT OFF — this is a
    DELIBERATE, CONSERVATIVE default and the one intentional divergence from R9-3 (which defaults
    ON). Rationale: schema.md is the FORMAL frontmatter contract (K6); an approved schema change
    alters how EVERY FUTURE ingest classifies and validates pages. A noisy or low-quality schema
    suggestion therefore has a much larger blast radius than a purpose.md note. We require the
    operator to opt in explicitly. Set false → no schema check runs (zero cost).
    Env var: SCHEMA_SUGGESTION_ENABLED.
    """

    schema_suggestion_max_tokens: int = 400
    """
    Hard output cap on the single bounded schema-pattern provider call (R9-4, AC READ: "bounded
    call max_tokens 400, no retry"). Larger than R9-3's 300 because the model must both restate
    the observed convention AND emit the exact markdown rule block to append.
    Env var: SCHEMA_SUGGESTION_MAX_TOKENS.
    """

    schema_suggestion_min_sources: int = 5
    """
    Throttle N (R9-4): the schema check fires only when at least this many `source` pages have
    been ingested since the newest existing schema-suggestion ReviewItem (cheap counter — a
    bounded indexed COUNT over pages.created_at, no new column, no migration). Default 5 (higher
    than R9-3's 3): a schema convention should be observed across MORE material before it is worth
    codifying. Below N → skip the provider call entirely (zero cost).
    Env var: SCHEMA_SUGGESTION_MIN_SOURCES.
    """

    schema_suggestion_timeout_seconds: float = 20.0
    """
    Timeout wrapping the single schema-pattern provider call (R9-4, I7). On timeout / any failure
    → no ReviewItem, ingest still completes. Env var: SCHEMA_SUGGESTION_TIMEOUT_SECONDS.
    """

    # ── F4: Graph drill-down (R9-5) ──────────────────────────────────────────────

    graph_cohesion_warn: float = 0.2
    """
    Low-cohesion warning threshold for community drill-down (R9-5, AC-R9-5-1).
    GET /graph/communities/{id} returns cohesion_warning=true when the community's
    intra-edge density is below this value, signalling a potentially fragmented community.
    Formula: cohesion = intraEdges / (size*(size-1)/2); range [0,1]; 0 for singletons.
    Env var: GRAPH_COHESION_WARN.
    """

    # ── F4: wikilink-enrichment post-pass bounds (ADR-0036, [AI] §9) ─────────────
    # The once-per-run enrichment call is bounded by these + the resolved provider row's
    # token_budget (I7). Substitution-apply (R1): the LLM returns {mention, target_title}
    # pairs; code validates + applies them single-mention into page BODIES only (I5).

    wikilink_enrich_enabled: bool = False
    """
    Master gate for the wikilink-enrichment post-pass (ADR-0036 §4).

    DEFAULT OFF since v1.7.0 (ADR-0076). nashsu/llm_wiki produces wikilinks INLINE during
    generation only — its enrich-wikilinks.ts is dead code — so link density is a function of the
    prompts (ingest/prompts.py restores the prominent wikilink instructions the 1.6.0 JSON scaffold
    buried). Keeping this post-pass ON on top of the parity prompts double-counts and makes the
    link-density parity band unfalsifiable, and it overshoots the reference on the delegated/CLI
    path (the 1:1 E2E). It remains one opt-in toggle away for zero-prompt-cost link recovery.
    Set true to re-enable one bounded call per orchestrated run. Env: WIKILINK_ENRICH_ENABLED.
    """

    wikilink_enrich_min_chars: int = 200
    """
    Anti-spam / cost gate (ADR-0036 §2.1 step 2): the enrichment LLM call runs only if the
    combined body length of the written pages is at least this many characters. Below the gate
    → zero substitutions, zero cost, zero LLM call. Env var: WIKILINK_ENRICH_MIN_CHARS.
    """

    wikilink_enrich_max_candidates: int = 500
    """
    Cap on the existing-page-title candidate list sent to the model (ADR-0036 §4, I7). Bounds
    the prompt size; when the vault exceeds this, the most-recent titles are kept (best-effort,
    §6 risk 2). Env var: WIKILINK_ENRICH_MAX_CANDIDATES.
    """

    wikilink_enrich_max_subs: int = 100
    """
    Hard cap on applied substitutions per run (ADR-0036 §4, Do-NOT #3/#6). The single LLM call's
    substitution list is truncated to this many — never an unbounded edit set.
    Env var: WIKILINK_ENRICH_MAX_SUBS.
    """

    wikilink_enrich_token_budget: int = 4_000
    """
    Fallback token budget for the single enrichment call (ADR-0036 §4, I7) when the resolved
    provider row carries none. Small: a compact body digest + a short substitution list fits.
    Env var: WIKILINK_ENRICH_TOKEN_BUDGET.
    """

    wikilink_enrich_timeout_seconds: float = 30.0
    """
    Timeout (seconds) wrapping the single enrichment provider call (ADR-0036 §4, I7).
    On timeout → apply zero substitutions (degrade, never fail ingest).
    Env var: WIKILINK_ENRICH_TIMEOUT_SECONDS.
    """

    # ── F11: Web clipper ingress (ADR-0038) ──────────────────────────────────────

    clip_enabled: bool = False
    """
    Master gate for the POST /clip ingress endpoint (F11, ADR-0038).
    Default OFF — must be explicitly enabled. When OFF the endpoint returns 503.
    Set CLIP_ENABLED=true to open the ingress (still requires CLIP_TOKEN).
    Env var: CLIP_ENABLED.
    """

    clip_token: str | None = None
    """
    SECRET. Bearer token required on every POST /clip request (F11, ADR-0038 §2.1).
    Compared constant-time (hmac.compare_digest). Missing/invalid → 401.
    NEVER logged. Set CLIP_TOKEN to a high-entropy random string.
    Env var: CLIP_TOKEN.
    """

    clip_allowed_origins: str = ""
    """
    Comma-separated allowlist of permitted request Origins (F11, ADR-0038 §2.2).
    Each entry is an exact origin string (scheme+host, no path/query).
    Example: "chrome-extension://abcdefghijklmnopqrstuvwxyz,http://127.0.0.1:5173"
    Empty string means only loopback/localhost requests are allowed (implicit).
    Env var: CLIP_ALLOWED_ORIGINS.
    """

    clip_max_body_bytes: int = 2 * 1024 * 1024  # 2 MB
    """
    Maximum allowed body size for POST /clip (F11, ADR-0038 §2.3, I7).
    Requests with Content-Length or accumulated body exceeding this value → 413.
    Default 2 MB — generous for any realistic Markdown clip.
    Env var: CLIP_MAX_BODY_BYTES.
    """

    @property
    def clip_allowed_origins_list(self) -> list[str]:
        """CLIP_ALLOWED_ORIGINS as a trimmed list (may be empty)."""
        return [o.strip() for o in self.clip_allowed_origins.split(",") if o.strip()]

    # ── K2: Lint-fix loop (ADR-0037 §4) ──────────────────────────────────────────
    # The K2 lint scan is a BOUNDED, HUMAN-GATED health check of the wiki. The single
    # semantic provider call (missing-xref / contradiction / stale-claim / missing-page)
    # is bounded by these + the resolved provider row's token_budget (I7). Deterministic
    # checks (orphan-page via the graph engine) make NO provider call.

    lint_max_iter: int = 3
    """
    Iteration cap for the bounded lint scan loop (ADR-0037 §4, I7). The loop is structurally
    capped at ``for n in range(1, LINT_MAX_ITER + 1)`` AND a token_budget gate at the top of
    each round. A caller may override (bounded 1..10) via POST /lint/scan; the value is FROZEN
    on the lint_runs row at INSERT and never re-read mid-loop. Env var: LINT_MAX_ITER.
    """

    lint_token_budget: int = 20_000
    """
    Token budget for one lint scan run (ADR-0037 §4, I7). The semantic provider call(s) stop
    when ``accumulator.total_tokens >= token_budget``. Caller-overridable (bounded 1_000..
    1_000_000); FROZEN on the lint_runs row at INSERT. Env var: LINT_TOKEN_BUDGET.
    """

    lint_max_findings: int = 50
    """
    Hard cap on findings emitted per lint run (ADR-0037 §4, Do-NOT — never an unbounded
    enqueue). Deterministic + semantic findings are merged and truncated to this many.
    Env var: LINT_MAX_FINDINGS.
    """

    lint_timeout_seconds: float = 30.0
    """
    Timeout (seconds) wrapping each semantic lint provider call (ADR-0037 §4, I7). On timeout →
    emit only the deterministic findings (degrade, never fail the scan). Env var:
    LINT_TIMEOUT_SECONDS.
    """

    # ── F12: Multi-format ingest (ADR-0025 §4.1) ─────────────────────────────────

    extract_max_chars: int = 2_000_000
    """
    Maximum characters of extracted text output (F12, I7 — pathological-file guard).
    Default ~2M chars (~500K tokens at 4 chars/token) — generous but bounded.
    Env var: EXTRACT_MAX_CHARS.
    """

    # ── R8-1: Pluggable PDF extractor seam (ADR-0051) ────────────────────────────

    pdf_extractor: str = "pypdf"
    """
    PDF extraction backend (ADR-0051, R8-1).
    Allowed values: "pypdf" (default, pure-Python, always available) | "marker"
    (high-quality ML extractor; requires the tools/marker-converter/service.py
    microservice to be running at MARKER_SERVICE_URL).
    When "marker" is selected and the microservice is unreachable or returns an error,
    extract.py falls back to pypdf unconditionally (PM decision — pypdf is never removed).
    Env var: PDF_EXTRACTOR.
    """

    marker_service_url: str = "http://host.docker.internal:8555"
    """
    Base URL of the Marker PDF extractor microservice (ADR-0051, R8-1).
    Used only when PDF_EXTRACTOR=marker. The backend POSTs the raw PDF bytes to
    {marker_service_url}/convert and expects JSON {"markdown": str, "pages": int}.
    Default points to the host machine from inside Docker (host.docker.internal).
    For local dev outside Docker, use http://localhost:8555.
    Env var: MARKER_SERVICE_URL.
    """

    marker_timeout_seconds: float = 1800.0
    """
    HTTP timeout (seconds) for the call to the Marker microservice (ADR-0051, R8-1, I7).
    Marker runs ML models and, for large PDFs, converts multiple page-range chunks inside a
    SINGLE /convert request (ADR-0065) — so the timeout must cover the whole chunked job, not
    one page. Default 1800 s (30 min) accommodates several-hundred-page ServiceNow exports; a
    ceiling, not a fixed wait (small PDFs finish in seconds). On timeout, extract.py falls back
    to pypdf (permanent, unconditional — PM decision).
    Env var: MARKER_TIMEOUT_SECONDS.
    """

    marker_max_upload_bytes: int = 314_572_800
    """
    Max PDF size for POST /ingest/convert-marker (ADR-0065). Default 300 MB — dedicated cap,
    SEPARATE from max_upload_bytes (25 MB, text/generic uploads), because Marker chunks large
    PDFs by page range so a 190 MB ServiceNow export is convertible without OOM. Only this
    endpoint uses it; every other upload path keeps the 25 MB limit.
    NOTE: uploads through a reverse proxy / Cloudflare Tunnel may hit a lower body cap (~100 MB
    on CF) regardless of this value — import very large PDFs over the LAN / Tailscale.
    Env var: MARKER_MAX_UPLOAD_BYTES.
    """

    # ── P3-d: MinerU cloud PDF extractor (ADR-0066/ADR-0069, opt-in, off by default) ──

    mineru_api_url: str = "https://mineru.net/api/v4"
    """
    Base URL of the MinerU CLOUD PDF extraction API (ADR-0069, v1.5 P3-d).
    Used only when PDF_EXTRACTOR=mineru. ⚠️ CLOUD PROVIDER (I9): selecting mineru uploads the
    raw PDF bytes to an external service. Opt-in, OFF by default (pypdf is the default). On any
    failure (no API key, non-2xx, timeout) extract.py falls back to pypdf unconditionally.
    Env var: MINERU_API_URL.
    """

    mineru_api_key: str = ""
    """
    MinerU cloud API token (ADR-0069, v1.5 P3-d). SECRET — env-only; NEVER exposed through the
    PUT /config/app/{key} surface (config_overrides §2.4). Empty → mineru extraction is a no-op
    that falls back to pypdf (the toggle can be selected in the UI, but nothing is uploaded until
    the operator sets this key in the environment). Env var: MINERU_API_KEY.
    """

    mineru_timeout_seconds: float = 600.0
    """
    HTTP timeout (seconds) for the MinerU cloud call (ADR-0069, I7). Default 600 s. On timeout
    extract.py falls back to pypdf (unconditional). Env var: MINERU_TIMEOUT_SECONDS.
    """

    # ── R8-2: Vision captions for images (F12 / F17) ─────────────────────────────

    vision_captions_enabled: bool = False
    """
    Master opt-in for vision captioning of ingested images (R8-2, F12). Default False (PM
    de-scope safety): when False, image files always take the extract.py placeholder path and
    NO provider vision call is ever made — zero cost, zero surprise. When True AND the resolved
    ingest provider reports capabilities().supports_vision, the orchestrator sha256-caches and
    captions image files (png/jpg/jpeg/gif/webp) before the normal analyze→generate flow.
    Env var: VISION_CAPTIONS_ENABLED.
    """

    vision_max_images_per_run: int = 5
    """
    Per-ingest-run cap on the number of images captioned via a provider vision call (R8-2, I7 —
    bounded loop / cost control). Beyond this cap, remaining images in the same run fall back to
    the extract.py placeholder. Cache HITS do NOT count against this cap (no provider call).
    Env var: VISION_MAX_IMAGES_PER_RUN.
    """

    # ── R8-3: Audio/video transcription via Whisper microservice (F12) ───────────

    av_transcription_enabled: bool = False
    """
    Master opt-in for audio/video transcription (R8-3, F12). Default False — zero behaviour
    change when not set. When True, AV extensions (.mp3, .wav, .m4a, .mp4) are sent to the
    Whisper microservice at WHISPER_SERVICE_URL/transcribe and the returned transcript is used
    as source_text for the normal analyze→generate flow. On ANY failure the existing placeholder
    path is used and a WARNING is logged. Never raises into ingest.
    Env var: AV_TRANSCRIPTION_ENABLED.
    """

    whisper_service_url: str = "http://host.docker.internal:8666"
    """
    Base URL of the Whisper transcription microservice (R8-3, F12).
    Used only when AV_TRANSCRIPTION_ENABLED=true. The backend POSTs the raw AV bytes to
    {whisper_service_url}/transcribe (multipart "file" field) and expects JSON:
    {"text": str, "language": str, "duration_seconds": float}.
    Default points to the host machine from inside Docker (host.docker.internal).
    For local dev outside Docker, use http://localhost:8666.
    Env var: WHISPER_SERVICE_URL.
    """

    whisper_timeout_seconds: float = 300.0
    """
    HTTP timeout (seconds) for the call to the Whisper microservice (R8-3, F12, I7).
    Whisper runs on CPU/GPU and can be slow for long media; 300 s (5 min) gives it room.
    On timeout, transcription.py returns None and ingest falls back to placeholder.
    Env var: WHISPER_TIMEOUT_SECONDS.
    """

    av_max_files_per_run: int = 3
    """
    Per-ingest-run cap on the number of AV files sent to the Whisper microservice (R8-3, I7).
    Beyond this cap, remaining AV files in the same run fall back to the placeholder. Prevents
    a bulk upload from hammering the Whisper service (which serialises via asyncio.Lock).
    Env var: AV_MAX_FILES_PER_RUN.
    """

    # ── R9-1: Cost alert threshold (AC-R9-1-2) ────────────────────────────────────

    cost_alert_threshold_usd: float = 5.00
    """
    Monthly spend threshold in USD for the cost alert flag (R9-1, AC-R9-1-2).
    When monthly_total_usd >= this value, GET /costs/summary returns threshold_alert=true.
    Set to 0.0 to disable the alert entirely (threshold_alert is always false).
    Default: 5.00 (as per AC-R9-1-2 spec).
    Env var: COST_ALERT_THRESHOLD_USD.
    """

    # ── M4-EXT: upload + scheduled import caps (ADR-0020 §2.4 / §4.4) ──────────

    max_upload_bytes: int = 26_214_400
    """
    Maximum file size for POST /ingest/upload (Feature U). Default 25 MB (I7).
    Env var: MAX_UPLOAD_BYTES.
    """

    import_scan_max_files: int = 200
    """
    Maximum number of files copied per scheduled scan tick (Feature S, I7).
    Env var: IMPORT_SCAN_MAX_FILES.
    """

    import_scan_max_seconds: int = 60
    """
    Wall-clock deadline (seconds) for one scheduled scan tick (Feature S, I7).
    Env var: IMPORT_SCAN_MAX_SECONDS.
    """

    import_scan_recursive: bool = False
    """
    Opt-in recursive folder import (R7-6). When True, the scheduled scan descends into
    subdirectories of the configured source_dir; when False (default), only the top-level
    directory is scanned (original behaviour). Recursion stays double-bounded by
    IMPORT_SCAN_MAX_FILES + IMPORT_SCAN_MAX_SECONDS (I7 — no unbounded traversal).
    Env var: IMPORT_SCAN_RECURSIVE.
    """

    ingest_max_concurrency: int = 3
    """
    Maximum number of watcher-driven ingests that may run CONCURRENTLY (I7).

    The watcher fires one task per changed file. Without a cap, dropping N files at once
    (e.g. a bulk copy into raw/sources/) launches N simultaneous ingests — each opening
    DB sessions, spawning a provider call (a full agent under the CLI backend) and an
    embedding request. On a single-GPU / small-RAM host this floods the DB pool, the
    embedding service and memory, and can OOM the box. A bounded semaphore parks the
    surplus and drains it a few at a time; every file is still processed, just not all at
    once. Coerced to ≥ 1 at read time. Keep well under the DB pool size (db.py).
    Env var: INGEST_MAX_CONCURRENCY.
    """

    # ── System self-update (R12-3, B1: Watchtower HTTP API) ───────────────────────

    watchtower_url: str | None = None
    """Base URL of the Watchtower HTTP API (e.g. ``http://watchtower:8080``).

    When set together with ``watchtower_http_api_token``, ``POST /ops/system-update`` pokes
    Watchtower's ``/v1/update`` to pull the latest images and recreate every container labelled
    ``com.centurylinklabs.watchtower.enable=true`` on the host. Unset ⇒ the update button is
    unsupported and the UI hides it (``update_supported=false``). Env: WATCHTOWER_URL."""

    watchtower_http_api_token: str | None = None
    """Bearer token for Watchtower's ``--http-api-update`` endpoint; must match the token Watchtower
    was started with. Never logged, env-only. Env: WATCHTOWER_HTTP_API_TOKEN."""

    update_check_repo: str = "Emanuele-Chiummo/llm-wiki-synapse"
    """``owner/repo`` whose latest GitHub Release tag is the "available version" for the update
    check (public API, no auth). Env: UPDATE_CHECK_REPO."""

    # ── Authentication (ADR-0052) ─────────────────────────────────────────────────

    deployment_mode: Literal["local", "server"] = Field(
        default="local",
        validation_alias=AliasChoices("SYNAPSE_DEPLOYMENT_MODE", "deployment_mode"),
    )
    """
    Runtime trust boundary for the shared REST API.

    ``local`` preserves the zero-config, loopback-oriented development experience and permits
    an empty ``SYNAPSE_AUTH_TOKEN``. ``server`` is fail-closed: startup validation requires a
    non-whitespace bearer token of at least 32 characters. Env var: SYNAPSE_DEPLOYMENT_MODE.
    """

    auth_token: str = Field(
        default="",
        validation_alias=AliasChoices("SYNAPSE_AUTH_TOKEN", "auth_token"),
    )
    """
    Shared Bearer token for the REST API (ADR-0052, R10-1, F16).

    Empty string or absent (the default) ⇒ authentication is DISABLED.
    All routes behave exactly as v0.9 — no 401s, no behaviour change.
    This is the backward-compatible default (EC-M10-11).

    Set (non-empty) ⇒ every non-exempt request MUST carry
    ``Authorization: Bearer <token>``, compared constant-time with
    ``secrets.compare_digest``. Absent/wrong token → 401.

    NEVER logged, NEVER stored in the DB, NEVER hashed (env-only by design —
    §2.1 of ADR-0052). Recommend ≥ 32 random characters.

    Env var: SYNAPSE_AUTH_TOKEN.
    """

    @model_validator(mode="after")
    def validate_server_auth(self) -> Self:
        """Fail closed when a network-facing deployment has no strong shared token."""
        if self.deployment_mode != "server":
            return self
        if not self.auth_token:
            raise ValueError("SYNAPSE_AUTH_TOKEN is required when SYNAPSE_DEPLOYMENT_MODE=server")
        if len(self.auth_token) < 32:
            raise ValueError(
                "SYNAPSE_AUTH_TOKEN must contain at least 32 characters in server mode"
            )
        if any(char.isspace() for char in self.auth_token):
            raise ValueError("SYNAPSE_AUTH_TOKEN must not contain whitespace in server mode")
        if len(set(self.auth_token)) < 8:
            raise ValueError(
                "SYNAPSE_AUTH_TOKEN must be randomly generated and contain at least "
                "8 distinct characters in server mode"
            )
        return self

    # ── MCP server introspection (F1-MCP-UI, ADR-0027 §2.3) ──────────────────────

    mcp_transport: str = "stdio"
    """
    MCP server transport type (ADR-0010 §1; ADR-0027 §2.3).
    Default: "stdio" — the transport the synapse MCP server uses.
    Env var: MCP_TRANSPORT.
    """

    mcp_entry_command: str = "python -m app.mcp.server"
    """
    Shell command to launch the MCP server (ADR-0027 §2.3).
    Default: "python -m app.mcp.server" — the documented stdio entry point.
    Env var: MCP_ENTRY_COMMAND.
    """

    # ── Provider API-key at-rest encryption (W1 / F17, §12 amendment) ──────────

    synapse_secret_key: str | None = None
    """
    SECRET. Master key for at-rest encryption of UI-supplied provider API keys
    (``provider_config.api_key_encrypted``). A urlsafe-base64 32-byte Fernet key
    (generate with ``python -c "from cryptography.fernet import Fernet;
    print(Fernet.generate_key().decode())"``).

    Read at call time from the environment inside app/secrets_crypto.py (never here) so the
    value is monkeypatch/hot-edit friendly; this field exists only to document the var and keep
    pydantic-settings from rejecting it. When unset/invalid: key storage is DISABLED — CRUD
    refuses to store UI keys (HTTP 400) and the provider layer falls back to env-var keys
    (ANTHROPIC_API_KEY / OPENAI_API_KEY). Never logged, never returned by any endpoint.
    Env var: SYNAPSE_SECRET_KEY.
    """

    # ── MCP HTTP remote surface (ADR-0029 §2.2 / §2.3; amended by ADR-0033) ─────

    mcp_auth_token: str | None = None
    """
    SECRET. Bootstrap bearer token for the /mcp/server HTTP surface (ADR-0029 §2.2).

    ADR-0033 §2.1 — precedence (most specific wins):
      1. vault_state.mcp_access_token_hash (UI-set token, PBKDF2 hash) — checked first.
      2. MCP_AUTH_TOKEN (this var, plaintext env bootstrap) — used iff DB hash is NULL.
      3. none — no token is configured.

    Existing deployments continue to work unchanged: if MCP_AUTH_TOKEN is set and no
    DB token has been set via PUT /mcp/auth, this env value is the active token.
    Never logged, never returned by any API endpoint.
    Env var: MCP_AUTH_TOKEN.
    """

    mcp_trusted_proxies: str = ""
    """
    Comma-separated list of trusted reverse-proxy IPs or CIDRs (ADR-0033 §2.3).

    When a request's immediate TCP peer (scope["client"][0]) matches one of these
    entries, the gateway reads the LAST X-Forwarded-For hop as the resolved client IP
    (the proxy-attested origin). When empty (default) — or when the peer is NOT in
    this list — X-Forwarded-For is IGNORED entirely (fail-safe against XFF spoofing).

    CF-Connecting-IP / CF-Ray are treated as PUBLIC *signals* regardless of this
    setting — their presence always forces PUBLIC classification, never grants private
    access (ADR-0033 §2.3 fail-safe).

    Default: "" (empty) ⇒ trust only the transport peer; XFF ignored.
    Env var: MCP_TRUSTED_PROXIES
    Example: "10.0.0.1,172.16.0.0/12"
    """

    mcp_remote_write_enabled: bool = False
    """
    Whether write_page is exposed on the HTTP MCP surface (ADR-0029 §2.3).
    Default false — read-only by default (defence-in-depth: even a leaked token
    cannot mutate the vault unless this flag is explicitly set).
    true  → write_page is included on the HTTP surface (still bearer-gated;
            still routes through write_wiki_page — ADR-0010 §2).
    false → only search_wiki, get_page, list_pages are exposed over HTTP.
    The stdio mcp server ALWAYS has all four tools, regardless of this flag.
    Env var: MCP_REMOTE_WRITE_ENABLED.
    """

    @property
    def mcp_http_enabled(self) -> bool:
        """
        True unconditionally (ADR-0033 §2.4 always-mount).

        The MCP HTTP capability is always compiled in. The middleware (_McpGate)
        is the sole per-request arbiter of reachability; mount condition is no
        longer "token set." The boolean is retained for backward-compat fields
        in McpInfoResponse (http_enabled alias).
        """
        return True

    @property
    def mcp_trusted_proxies_list(self) -> list[str]:
        """Parsed MCP_TRUSTED_PROXIES as a trimmed list (may be empty)."""
        return [p.strip() for p in self.mcp_trusted_proxies.split(",") if p.strip()]

    # ── B2: Chat composer — web-search + retrieval-mode (C2/C3) ─────────────────

    chat_web_max_results: int = 5
    """
    Maximum SearXNG results fetched per web-search-enabled chat turn (B2-C2, I7).
    Single-shot, no loop. Env var: CHAT_WEB_MAX_RESULTS.
    """

    chat_web_fetch_max_chars: int = 8_000
    """
    Per-URL content cap (chars) after HTML→markdown extraction in chat web-search (B2-C2, I7).
    Prevents a single large page from blowing the context. Env var: CHAT_WEB_FETCH_MAX_CHARS.
    """

    local_first_min_hits: int = 3
    """
    Minimum wiki retrieval hits required before the web-search gate opens in `local_first`
    mode (B2-C2, C3). When wiki citations < this value, web-search runs as fallback.
    Env var: LOCAL_FIRST_MIN_HITS.
    """

    # ── R13-9: In-process rate limiting for inference-cost endpoints (B4) ────────

    rate_limit_enabled: bool = True
    """
    Master gate for the per-IP fixed-window rate limiter (R13-9, B4).
    When False, no rate limiting is applied to inference-cost endpoints — useful for
    CI/dev environments where the test suite would hit the limit.
    Default True (rate limiting active).
    Env var: RATE_LIMIT_ENABLED.
    """

    rate_limit_requests: int = 20
    """
    Maximum requests per ``RATE_LIMIT_WINDOW_SECONDS`` per client IP (R13-9, B4).
    Applied to POST /chat/stream, POST /ingest/trigger, POST /ingest/upload,
    POST /ingest/from-text, POST /research/start.
    Default 20 (generous enough for normal use; tight enough to deter abuse).
    Env var: RATE_LIMIT_REQUESTS.
    """

    rate_limit_window_seconds: int = 60
    """
    Fixed-window duration in seconds for the per-IP rate limiter (R13-9, B4).
    Default 60 s — 20 requests per minute sustained.
    Env var: RATE_LIMIT_WINDOW_SECONDS.
    """


# Module-level singleton — import with `from app.config import settings`
settings = Settings()
