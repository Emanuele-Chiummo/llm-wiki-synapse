---
title: Sample Import
type: concept
sources: []
---

# Sample Import

This is a sample file placed in the `./import/` host folder to verify Feature S
(scheduled folder import, ADR-0020).

When the `./import:/import:ro` mount is active and the schedule is configured with
`source_dir=/import`, this file is copied into `vault/raw/sources/sample-import.md`
on each scan tick, and the watchdog then ingests it into Postgres + Qdrant.

## Key points

- Source files in `./import/` are READ-ONLY from Synapse's perspective.
- Synapse copies new/changed files into `vault/raw/sources/` — it never writes back.
- Only `.md`, `.txt`, and `.markdown` files are copied (F12/M5 boundary).
- Unchanged files (same sha256 hash) are skipped (I1 — incremental index only).

## Updated at test time

This line was appended to trigger a content-hash change.
