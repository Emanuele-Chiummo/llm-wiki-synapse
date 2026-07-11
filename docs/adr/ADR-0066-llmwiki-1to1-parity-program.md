# ADR-0066 — LLM Wiki 1:1 parity program (v1.5): invariant amendments + scope

- **Status:** Accepted
- **Date:** 2026-07-10
- **Sprint:** v1.5 — "LLM Wiki 1:1 parity"
- **Amends:** **I9** (was "SearXNG only, never Tavily") and the Marker-only PDF posture
  (ADR-0051). **Extends:** ADR-0007/0008 (InferenceProvider + provider_config), ADR-0051
  (Marker extractor), ADR-0033/0040 (MCP/HTTP surface), the v0.x parity program.
- **Reference:** live ultra-detailed UI map `docs/reference/LLMWIKI-LIVE-UI-MAP-2026-07-10.md`
  (supersedes the `llm_wiki-audit/` docs anchored to llm_wiki v0.5.4).

---

## 1. Context

The owner requires Synapse's desktop UI to be a **1:1 mirror** of the current nashsu/llm_wiki
build (much newer than the v0.5.4 the prior parity program audited). A fresh live mapping
(controlling the actual app) found Synapse already ~80% aligned, with a defined gap set and,
critically, **two divergences that are protected by Synapse invariants**. The owner explicitly
chose to **mirror LLM Wiki literally**, accepting the invariant changes this requires.

## 2. Decision

Run **v1.5** as a phased program to reach 1:1 UI + functional parity, and formally amend the
two blocking invariants.

### 2a. Invariant amendments (owner-approved)
- **I9 (web search):** SearXNG stays the **default, bundled, privacy-preserving** backend, but
  **Tavily · SerpApi · Firecrawl · Brave · Ollama Web** become **opt-in, off-by-default**
  providers, selectable in Settings (mirrors LLM Wiki "External Information Sources"). Cloud
  providers must warn about content leaving the machine and must never auto-enable.
- **PDF parser (ADR-0051 posture):** Marker-local stays the default; **MinerU cloud** becomes an
  **opt-in, off-by-default** toggle (mirrors LLM Wiki "MinerU PDF"), with the same upload warning.

Rationale for keeping them opt-in/off: preserves the privacy-first default and the "reuse
existing infra" spirit of I9 while satisfying the literal-mirror requirement. Enabling a cloud
provider is always an explicit user action.

### 2b. Scope — 6 phases (tracked in `docs/process/PROGRAM-v1.5-LLMWIKI-PARITY.md`)
- **P0 — Foundations:** this ADR + I9 amendment + program tracker. *(done in this change.)*
- **P1 — Vault config & Files:** editable `purpose.md`/`schema.md` in-app; whole-vault file tree
  + "Open project folder" (vs today's read-only `MetaFileView` + raw-only `SourcesView`).
- **P2 — Multi-vault Project Launcher:** the ⇄ bottom-rail entry → launcher (New Project / Open
  Project / Recent Projects). Backend: multi-vault registry + active-vault switch (`vault_id` is
  already plumbed). Frontend: launcher screen + recent-projects store. *(largest phase.)*
- **P3 — Settings parity:** Image Captioning, Network proxy, Scheduled Import (external dir),
  Source Watch wider allowed-types + grouped UI, **MinerU toggle**, **multi-provider web-search
  section**; decide flat-15 vs grouped IA.
- **P4 — Chat composer:** Skills button, AnyTXT toggle, `Fast/Standard/Deep/Local first` mode pills.
- **P5 — Skills view:** rail entry #10 — scan skill folders, enable/disable/rescan for Chat.

Each phase: green tests + architect review + docs (D-artifacts, ER if schema changes) + **live
preview** + owner checkpoint.

## 3. Consequences
- Synapse gains cloud-capable web search + PDF parsing, **off by default**; privacy posture
  preserved unless the user opts in.
- Multi-vault is a real data-model/UX addition (single-vault posture from v1.0 is retired); every
  vault-scoped query already carries `vault_id`, so the change is additive, not a rewrite.
- The stale `llm_wiki-audit/` + `SYNAPSE-VS-LLMWIKI-PARITY.md` "full parity" verdict is superseded
  by the live map; parity is re-opened against the current LLM Wiki build.

## 4. Alternatives considered
- **Keep I9 (SearXNG-only), mirror only the UI concept.** Rejected by the owner — they want the
  literal provider set.
- **Fold into 1.4.1.** Rejected — 1.4.1 is a shipped patch (Marker chunking + graph fix); this is
  a milestone-scale program.
