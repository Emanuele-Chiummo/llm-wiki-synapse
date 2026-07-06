<!--
  Read CONTRIBUTING.md and CLAUDE.md first — they are the source of truth for
  conventions, invariants, and feature IDs. PRs target `main`; tags are only
  ever cut from `main`.
-->

## Summary

<!-- What does this PR change and why? One or two sentences. -->

## Related feature / roadmap IDs

<!-- Reference at least one: K1–K8, F1–F18, or a roadmap/debt ID (T*, R13-*, B*). -->

Closes #

## Type of change

- [ ] `feat` — new capability
- [ ] `fix` — bug fix
- [ ] `refactor` — no behavior change
- [ ] `docs` — documentation only
- [ ] `test` / `ci` / `chore`
- [ ] Breaking change (documented below)

## Checklist

- [ ] Tests pass: `make test` (pytest + vitest)
- [ ] Linters clean: `make lint` (ruff + black + mypy + eslint + prettier)
- [ ] ER diagram regenerated if `models.py` / a migration changed: `make er`
- [ ] OpenAPI JSON current if routes changed: `make openapi`
- [ ] Any new bounded loop has `max_iter` + `token_budget` + cost logging (I7)
- [ ] No hardcoded model IDs — read from `provider_config` (I6)
- [ ] Graph layout stays server-side; no main-thread force layout (I2)
- [ ] ADR filed for significant architectural decisions
- [ ] D-artifacts updated per the docs gate (CLAUDE.md §9), if applicable

## Notes for reviewers

<!-- Screenshots for UI changes, migration steps, anything non-obvious. -->
