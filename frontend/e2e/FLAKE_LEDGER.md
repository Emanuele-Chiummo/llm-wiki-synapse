# Playwright Flake Ledger

Track every test that was retried in CI (`retries: 1` in `playwright.config.ts`).
When a test flakes, add a row. When the root cause is fixed, mark it resolved.

## Format

| Date | Test file | Test name | Failure summary | Root cause | Resolution | Status |
|------|-----------|-----------|-----------------|------------|------------|--------|
| YYYY-MM-DD | e2e/foo.spec.ts | `test name` | what the assertion said | why it happened | PR / commit that fixed it | open / resolved |

## Active flakes

_None yet._

## Resolved flakes

_None yet._

## Policy

- A test that appears in this ledger 3+ times with no root cause identified must be
  quarantined (`.skip`) until investigated. Open a GitHub issue and link it here.
- `retries: process.env["CI"] ? 1 : 0` is set in `playwright.config.ts`. A trace
  ZIP is saved automatically on first retry (`trace: "on-first-retry"`). Download
  from the CI artifacts and open with `npx playwright show-trace trace.zip`.
- Do not increase `retries` beyond 1 without architect sign-off.
