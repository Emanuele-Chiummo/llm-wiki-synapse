# Contributing to Synapse

Synapse is a solo evenings project. This document records the conventions so that
future contributors (including AI agents) operate consistently with the existing
codebase. Read CLAUDE.md first — it is the single source of truth for architecture,
invariants, and feature IDs.

---

## Branching policy

- `main` is the integration branch and the only branch from which release tags are cut.
- Every sprint or feature branch is cut from `main`:
  - Sprint branches: `sprint/vX.Y`
  - Feature branches: `feature/<short-description>`
  - AI-agent session branches: `claude/<slug>`
- Work happens on the feature/sprint branch. A PR is opened against `main`.
- **Squash merges are preferred** for sprint branches to keep `main` linear.
- Branch protection on `main`: direct pushes are blocked; all changes arrive via PR.

---

## Commit format

```
<type>(<module>): <description> [Fxx / Kxx]
```

- `type`: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`
- `module`: the affected area (e.g. `ingest`, `graph`, `provider`, `ui`, `docs`, `ci`)
- `description`: imperative present tense, ≤72 characters on the summary line
- Every commit **must** reference at least one feature ID from CLAUDE.md §4 (K1–K8 or F1–F18)
  or a structural-debt ID from the roadmap (T1–T10, R13-x, etc.)

Examples:
```
feat(provider): add OllamaProvider with structured JSON output [F17]
fix(graph): offload FA2 recompute to executor — eliminate event-loop stall [B1]
docs(adr): add ADR-0057 responsive mobile/tablet layout [F1, F15]
```

Breaking changes: add `!` after the type/module and document in the PR body.

---

## Release rule

**Tags are only ever cut from `main`.** No tag is cut from a feature branch,
a sprint branch, or any commit that has not been merged to `main`.

This rule was established in v1.3 (R13-2) to fix the T2 structural debt where
v1.2.4–v1.2.6 were tagged from unmerged commits. The consequence of violating
this rule is that `main` (the OSS front door) diverges silently from the shipped
product — a maintenance hazard and a trust problem.

---

## Version bumps

Use the single-command version bump script that updates all four version files in
one atomic commit:

```
make bump VERSION=x.y.z
```

This updates (in one operation):
- `src-tauri/tauri.conf.json` — Tauri desktop app version
- `src-tauri/Cargo.toml` — Rust package version
- `frontend/package.json` — npm package version
- `backend/pyproject.toml` (or equivalent) — Python package version

Do not edit these files independently; the CI version-consistency check will fail.

The `make bump` target was introduced in v1.3 (R13-8) to replace the manual 4-file
ritual that was a source of version-skew errors.

---

## Pull request checklist

Before opening a PR against `main`:

- [ ] All existing tests pass: `make test` (pytest + vitest)
- [ ] Linters clean: `make lint` (ruff + black + mypy + eslint + prettier)
- [ ] ER diagram regenerated if `models.py` or an Alembic migration changed: `make er`
- [ ] OpenAPI JSON is current: `make openapi` (docs-gate CI job diffs this)
- [ ] Any new bounded loop has `max_iter` + `token_budget` + cost logging (I7)
- [ ] ADR filed for significant architectural decisions (route via solution-architect)
- [ ] D-artifacts updated as required by the docs-gate (CLAUDE.md §9)
- [ ] The PR description references the relevant feature IDs and roadmap items

---

## Coding standards

| Layer | Linting | Types | Tests |
|-------|---------|-------|-------|
| Backend (Python 3.11+) | ruff + black | mypy strict | pytest (unit + integration) |
| Frontend (TypeScript) | eslint + prettier | TypeScript strict | vitest + Playwright |

- No secrets in code; all configuration via env vars + docker-compose / `.env`
- No hardcoded model IDs; always read from `provider_config` (I6)
- All loops must be bounded: `max_iter` cap + `token_budget` + `total_cost_usd` logged (I7)
- Graph layout runs server-side only — never on the UI main thread (I2)

See CLAUDE.md §12 for the full standards reference.

---

## Docs-as-DoD (I8)

No sprint is done without its documentation artifacts updated:

- Mermaid diagrams (`docs/architecture/`, `docs/sequences/`) updated when topology changes
- ER diagram regenerated via `make er` when the schema changes
- ADR filed for each significant architectural decision
- `docs/process/DOCS_STATUS.md` verdict must read `ALL UP-TO-DATE` before tagging

D5 screenshots are captured by the CI E2E job (R13-8) or manually via `make screenshots`
against a live stack. They are non-blocking for the code gate but blocking for the
human-checkpoint milestone tag.
