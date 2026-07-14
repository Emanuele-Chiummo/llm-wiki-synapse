# ADR-0081 — New-vault onboarding wizard, scenario templates, and per-vault output language (v1.7.0)

- **Status:** Accepted
- **Date:** 2026-07-14
- **Amends:** ADR-0067 (multi-vault Project Launcher)
- **Migration:** 0032 (`vault_state.output_language`, nullable)
- **Invariants touched:** I5, I6, I8
- **Reference:** `docs/reference/LLMWIKI-CORE-LOGIC-v0.6.3.md` §4

## Context

nashsu/llm_wiki's create-project dialog asks for **Name + AI Output Language (mandatory, "auto"
excluded) + Parent Directory + Template**, then scaffolds the vault and overwrites `schema.md` /
`purpose.md` from the chosen template (which also creates template-specific extra directories and
custom page types). Synapse's launcher asked only name + path, did **not** auto-activate the new
vault (the user had to click the row → hard reload), and kept scenario templates siloed in
Settings ("Modelli di scenario") applying only to the already-active vault. There was no per-vault
output language — ingest detected it per source.

## Decision

### 1. Scenario templates carry structure, not just prose

`scenarios_data.py` ports llm_wiki's 5 templates (research, reading, personal-growth, business,
general) with a proper `## Page Types` table (base 6 rows + the template's custom types in
`wiki/<dir>/` form, consumed by ADR-0077 routing) and an `extra_dirs` list. A short Synapse
addendum ("content pages must carry a non-empty `sources:`") is appended rather than editing the
verbatim body.

### 2. Scaffold applies the scenario at creation

`bootstrap_vault_at(vault, *, scenario_id=None, output_language=None)` overwrites root
`schema.md` / `purpose.md` from the template, creates its `extra_dirs`, seeds `index.md` with
per-type sections plus a code-owned empty `## Recently Updated` section (the bounded catalogue
that ingest appends to — WS-B), and writes `log.md`'s first `- Project created` entry.
`POST /projects` accepts `scenario` and `output_language` (400 on an unknown scenario) and
persists `output_language` into the new vault's `vault_state` row. `apply_scenario` (Settings
re-apply) now also creates `extra_dirs`.

### 3. Per-vault output language is stored, not re-detected

Migration 0032 adds nullable `vault_state.output_language` (ISO-639-1; NULL = auto / pre-1.7.0
vault). The block ingest prompts (ADR-0076) read it to emit the MANDATORY OUTPUT LANGUAGE
directive; when NULL they fall back to per-source detection. `GET`/`PUT /vault/meta/output-language`
expose it for later editing in Settings.

### 4. Onboarding is a wizard that auto-activates

The frontend `NewProjectWizard` (modal, reusing the `FirstRunWizard` pattern) walks Name +
Parent Directory → mandatory Output Language ("auto" excluded) → Template (5 cards, general
default) → Create, then **activates the new vault and reloads** — closing the no-auto-activate
gap. The Settings scenario cards remain for re-applying to an existing vault.

## Consequences

- New-vault UX matches llm_wiki: language and scenario are chosen up front and take effect at
  scaffold time, and the vault is immediately usable.
- I6 preserved: `output_language` is data read by the provider-neutral prompt builders, not a
  provider branch. I5 preserved: templates keep `wiki/` a valid Obsidian vault.
- Existing vaults are unaffected (output_language NULL → prior auto behavior; no page rewrite).
- I8: this ADR, migration 0032 + regenerated ER (D2), the regenerated OpenAPI (D4), and
  `test_projects_create_scenario.py` (30 cases) satisfy the docs gate; the frontend wizard adds
  D5 screenshots in a later PR.
