# ADR-0038 — F11 Web Clipper: secure clip ingress model

- **Status:** Accepted
- **Date:** 2026-06-30
- **Sprint:** v0.6 (M6 — shippable; F11 Web Clipper + secure local ingress)
- **Features:** F11 (Chrome MV3 web clipper + secure local clip ingress + auto-ingest) ·
  K1 (3-layer vault: raw/ → wiki/) · I1 (incremental index only) · I5 (atomic vault writes;
  Obsidian-valid wiki/) · I7 (bounded; body cap enforced)
- **Reference:** R1 (nashsu/llm_wiki — the clip server this ADR explicitly improves upon) ·
  docs/reference/llm_wiki-audit/02-CODE-UI-REVIEW.md §1 (findings S-1..S-6)
- **Invariants owned:** **I5** (atomic write via temp+os.replace; wiki/ never touched directly) ·
  **I1** (watcher's mtime/SHA gate deduplicates re-clips; no double-ingest) ·
  **I7** (body cap prevents DoS; CLIP_MAX_BODY_BYTES env-configured)
- **Author:** backend-engineer · solution-architect

---

## 1. Context

F11 adds a Chrome MV3 web clipper that converts pages to Markdown (via Readability +
Turndown in the browser) and sends them to a Synapse ingress endpoint for auto-ingest into
`vault/raw/sources/`.

The reference app `nashsu/llm_wiki` ships a clip server (`:19827`) with **five critical
security defects** documented in `docs/reference/llm_wiki-audit/02-CODE-UI-REVIEW.md §1`:

| Audit ID | Defect | Severity |
|----------|--------|----------|
| S-1 | Clip server completely unauthenticated | Critical |
| S-2 | Caller controls the base directory (`project_path`) — writes to arbitrary paths | Critical |
| S-3 | Drive-by cross-origin POST: CORS is checked only for response headers, not to gate the action; any visited website can trigger ingest of attacker-controlled content | Critical |
| S-4 | LAN exposure with no token (inherits bind host from the API server) | High |
| S-5 | No body size cap — trivial DoS | High |
| S-6 | Content not sanitised before reaching the LLM pipeline | Medium |

**Synapse must not repeat any of these.** This ADR documents the security model that
closes all six gaps.

---

## 2. Decision

### 2.1 Single service — no second server (S-4 closed by architecture)

The clip ingress is **`POST /clip` on the existing FastAPI service** — not a second process
on a separate port. This eliminates S-4 by construction: the existing service binds on the
operator's configured host:port (not `0.0.0.0` by default), benefits from all the existing
security controls (rate-limiting, reverse proxy, Tailscale), and adds no new network surface.

The reference app's `:19827` separate server was the root cause of S-4: it ran with different
(unauthenticated) logic from the API on `:19828`. Synapse has exactly ONE service.

### 2.2 AuthN: CLIP_TOKEN (closes S-1)

Every `POST /clip` request **must** carry `Authorization: Bearer <CLIP_TOKEN>`. The token is:
- Configured via the `CLIP_TOKEN` env var (never hardcoded).
- Compared **constant-time** (`hmac.compare_digest`) to prevent timing side-channels.
- **Never logged**, never returned in any API response.
- Absent → 401; wrong → 401. Fail-closed (no configured token = always 401).

An optional master gate `CLIP_ENABLED` (default `false`) allows the ingress to be disabled
entirely even when the FastAPI app is running. When `CLIP_ENABLED=false` → 503 before any
auth check.

### 2.3 Origin/Host allowlist (closes S-3)

**CORS alone does not protect against drive-by `POST` writes.** A "simple" `POST` with
`Content-Type: application/json` does not trigger a preflight check in some browser contexts,
and even when it does, the CORS gate only controls *response* headers — the server action has
already executed by the time the browser sees the response.

Therefore, the server validates the `Origin` header **before any processing or disk write**:

```
allowlist = CLIP_ALLOWED_ORIGINS (env, comma-separated) ∪ loopback origins (implicit)
```

Implicit loopback: `http://localhost`, `http://127.0.0.1`, `http://[::1]`, and their Vite
dev-server variants (`:5173`). These are always allowed because they are bounded to the
local machine and the token gate is still enforced.

The extension owner configures `CLIP_ALLOWED_ORIGINS=chrome-extension://<extension_id>` to
permit requests from their installed extension.

When the `Origin` header is absent (non-browser curl/local automation), the check passes —
the token gate alone is sufficient for non-browser paths (there is no CSRF risk without a
browser context).

Decision logic:
```
if origin is None → allow (non-browser; token is the auth)
if origin in (CLIP_ALLOWED_ORIGINS ∪ loopback) → allow
else → 403
```

This check is enforced **server-side** on every request, independent of CORS middleware.

### 2.4 Body cap (closes S-5)

`CLIP_MAX_BODY_BYTES` (env, default 2 MB) caps the accepted body size. Two enforcement
points:
1. Check `Content-Length` header **before deserialisation** → 413 if exceeded.
2. Check the size of the already-deserialised JSON body (belt-and-braces) → 413.

The reference app used `read_to_string` with no limit. 2 MB covers any realistic web article.

### 2.5 Safe path: no caller-supplied base directory (closes S-2)

The caller supplies `{url, title, markdown}`. The **server** derives the filename from
`title` (never from a caller-supplied path):

```python
filename = _clip_safe_filename(title, url)  # sanitize: strip /\\:*?"<>|, clamp 180 chars
name     = safe_source_name(filename)        # existing ADR-0020 sanitizer
dst      = resolve_under_sources(name)       # containment check: must start with raw_sources_dir/
```

The `resolve_under_sources()` function uses `Path.resolve()` + prefix check (existing
ADR-0020 §2.2 primitive). A path that escapes `vault/raw/sources/` raises 400 before any
write.

The caller **never** supplies a `project_path` or base directory. This closes S-2 by
removing the attack surface entirely.

### 2.6 Atomic write (I5)

The file is written via `tempfile.mkstemp` + `Path.replace()` (same-directory atomic
rename) so a crash mid-write cannot corrupt the vault.

### 2.7 Auto-ingest via existing pipeline (I1, no new ingest path)

After the atomic write, the **existing watchdog observer** sees the file appear in
`vault/raw/sources/` and calls `ingest_file()` through the normal pipeline. No new ingest
logic. The watcher's mtime/SHA gate (ADR-0001) deduplicates re-clips of unchanged content
(same URL + same Markdown body → `skipped` status, no double-ingest).

### 2.8 Content note (S-6 partial)

The Markdown is written verbatim to `raw/sources/`. The ingest pipeline (Obsidian-valid
frontmatter, LLM analysis/generation into `wiki/`) is downstream of this write. The raw file
is human-readable and the Obsidian-valid YAML frontmatter is constructed by the server (not
caller-supplied). The caller supplies only `url`/`title`/`markdown` — field values are
escaped before embedding in YAML (no YAML injection via `"`).

Full HTML sanitization (rehype-sanitize style) is deferred: the Readability + Turndown
pipeline in the extension already strips script/style/iframe before converting to Markdown,
and the LLM ingest analysis is bounded (I7) and runs in a sandboxed provider — not a direct
exec path for injected content.

---

## 3. Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `CLIP_ENABLED` | `false` | Master gate — must be `true` to open the ingress |
| `CLIP_TOKEN` | *(none)* | SECRET bearer token (required; no configured token = always 401) |
| `CLIP_ALLOWED_ORIGINS` | `""` (empty) | Comma-separated extra origins (e.g. `chrome-extension://abc`) |
| `CLIP_MAX_BODY_BYTES` | `2097152` (2 MB) | Body cap (I7) |

---

## 4. Chrome MV3 Extension

**Location:** `extension/` (repo root)

| File | Purpose |
|------|---------|
| `manifest.json` | MV3 manifest: permissions `activeTab`, `scripting`, `storage` |
| `popup.html` + `popup.js` | Clip UI: extract via Readability+Turndown, POST to `/clip` |
| `options.html` + `options.js` | Settings: base URL, token, extension ID display |
| `vendor/Readability.js` | `@mozilla/readability` (Apache-2.0, vendored) |
| `vendor/turndown.js` | `turndown` UMD build (MIT, vendored) |
| `vendor/Readability-LICENSE.md` | Apache-2.0 licence for Readability |
| `vendor/turndown-LICENSE` | MIT licence for Turndown |

**Vendoring approach:** Both libraries installed via `npm` (registry.npmjs.org), then the
UMD/browser-compatible dist files copied to `extension/vendor/`. No bundler required — the
MV3 extension executes them via `scripting.executeScript(target, files=[...])` in the page
context. Licences preserved in `vendor/`.

**Extension flow:**
1. User clicks the toolbar icon → popup opens.
2. Popup injects `vendor/Readability.js` + `vendor/turndown.js` into the active tab via
   `scripting.executeScript`.
3. An extraction script runs in the page: `new Readability(docClone).parse()` →
   `new TurndownService().turndown(article.content)`.
4. Title (editable) is shown. User clicks "Clip to Synapse".
5. Popup posts `{url, title, markdown}` to `{baseURL}/clip` with
   `Authorization: Bearer {token}` header.
6. Chrome sets the `Origin: chrome-extension://<id>` header automatically on the cross-origin
   fetch — the server validates it against `CLIP_ALLOWED_ORIGINS`.

**First-run setup:**
1. Install the extension (developer mode: load unpacked from `extension/`).
2. Open the extension Options page (right-click icon → Options).
3. Set `Synapse Base URL` (e.g. `http://localhost:8000`).
4. Generate a strong random token and set it as `CLIP_TOKEN` in Synapse env; paste it here.
5. Copy the Extension ID shown on the Options page.
6. Add `CLIP_ALLOWED_ORIGINS=chrome-extension://<that-id>` to Synapse env.
7. Set `CLIP_ENABLED=true` in Synapse env.

---

## 5. REST endpoint contract

```
POST /clip
Authorization: Bearer <CLIP_TOKEN>
Content-Type: application/json
Origin: chrome-extension://<extension_id>   (set by Chrome automatically)

{
  "url": "https://...",
  "title": "Article Title",
  "markdown": "# Article Title\n\nBody...",
  "source": null   // optional; defaults to url
}

→ 202 { "file_path": "raw/sources/Article-Title.md", "status": "queued", "overwritten": false }
→ 401  missing/invalid token
→ 403  origin not in allowlist
→ 413  body exceeds CLIP_MAX_BODY_BYTES
→ 400  unsafe/traversal filename
→ 503  CLIP_ENABLED=false
```

---

## 6. Security audit checklist (closes llm_wiki S-1..S-6)

| Audit finding | Synapse answer | Test |
|---|---|---|
| S-1 unauthenticated | CLIP_TOKEN bearer, constant-time compare, fail-closed | TC-CLIP-01, TC-CLIP-02 |
| S-2 attacker-controlled path | Server derives filename from title; safe_source_name + resolve_under_sources containment check | TC-CLIP-05 |
| S-3 drive-by cross-origin | Origin header validated server-side BEFORE processing; not CORS-only | TC-CLIP-03 |
| S-4 LAN exposure unauthenticated | Single service (no second server); CLIP_TOKEN always required | Architecture (no separate port) |
| S-5 no body cap | CLIP_MAX_BODY_BYTES gate (Content-Length + accumulated bytes) | TC-CLIP-04 |
| S-6 content not sanitised | Readability+Turndown strip scripts; YAML fields escaped; raw stored, not exec'd | TC-CLIP-06 (frontmatter check) |

---

## 7. Do-NOT list

1. **DO NOT** bind a second HTTP server for clip ingress (closes S-4 permanently).
2. **DO NOT** accept a `project_path` or base directory from the caller (closes S-2).
3. **DO NOT** rely on CORS alone to block cross-origin writes (closes S-3).
4. **DO NOT** log the CLIP_TOKEN plaintext or hash — not even a prefix.
5. **DO NOT** accept clip requests when `CLIP_ENABLED=false` (master gate — 503 first).
6. **DO NOT** write to `vault/wiki/` directly from the clip path (K1 layer separation; I5).
7. **DO NOT** set `CLIP_ALLOWED_ORIGINS=*` in production — always explicit allowlist.
8. **DO NOT** use a short token (< 32 chars recommended) — enforce this in documentation.
9. **DO NOT** bypass the watcher (call `ingest_file` directly) — let the watchdog observe
   the file write (I1 — the mtime/SHA gate handles idempotency).
10. **DO NOT** add Tavily or any alternative search for deep-research of clipped pages (I9 —
    existing SearXNG pipeline handles follow-up research).
