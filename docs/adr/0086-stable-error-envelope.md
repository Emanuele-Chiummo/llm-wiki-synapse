# ADR-0086 — Stable JSON error envelope across backend, frontend, and MCP (2.0.0)

- **Status:** Accepted
- **Date:** 2026-07-17
- **Invariants touched:** I8 (docs-as-DoD: OpenAPI regenerated)
- **Supersedes (partially):** the 1.9.2 non-regression contract in `app/errors.py`
  (the deliberately deferred wire-format change now lands)
- **SemVer:** MAJOR — breaking change (2.0.0 "One engine", breaking-change item 3)

## Context

Since 1.9.2 the backend has raised a domain exception taxonomy (`SynapseError` + 12
subclasses in `backend/app/errors.py`) through a single global handler
(`synapse_error_handler`). That handler was **deliberately** built to reproduce the exact
same wire shape a raw `fastapi.HTTPException` already produces — `{"detail": "..."}` — so
1.9.2 could ship the taxonomy infrastructure with zero observable behaviour change and defer
the actual envelope change to 2.0.0. This ADR is that deferred work.

The `{"detail": ...}` shape has three problems as a public contract:

1. **No machine-readable code.** Consumers (web frontend, iOS, MCP clients, external API
   users) branch on parsed message *strings*, which are human-facing and unstable.
2. **Two different shapes for errors.** A `HTTPException` produces `{"detail": "<string>"}`,
   but FastAPI's built-in `RequestValidationError` (Pydantic 422s) produces
   `{"detail": [ {loc, msg, type}, ... ]}` — an *array*. Frontend code had to special-case
   this (`providerClient.formatDetail`) to avoid rendering `[object Object]`.
3. **No status in the body.** Consumers that only see the parsed JSON (not the transport
   layer) can't read the status code.

The frontend already centralised error parsing in `frontend/src/api/errors.ts`
(`ApiError` + `checkResponse`) in 1.9.2 — though in practice ~13 API clients still carry an
inline copy of the same `body.detail` parse (they predate the extraction). All of them must
move to the new shape; this ADR treats `errors.ts` as the single source of *shape knowledge*
and has every client read the envelope through a shared helper.

## Decision

### 1. The envelope

Every error response — from any source — is wrapped in a single stable shape:

```json
{ "error": { "code": "not_found", "message": "Page 42 not found", "status": 404, "details": null } }
```

| Key       | Type                 | Meaning |
|-----------|----------------------|---------|
| `code`    | `string` (snake_case)| Stable, machine-readable slug. Public contract. |
| `message` | `string`             | Human-readable text — the exact wording `detail` carried before. |
| `status`  | `int`                | HTTP status, duplicated in the body for JSON-only consumers. |
| `details` | `object \| array \| null` | Optional structured payload (field-level validation errors); `null`/absent for simple errors. |

This is the **only** shape after 2.0.0. There is no dual-shape / backward-compatible mode
and no deprecation grace period (SemVer MAJOR). Status codes and message wording are
preserved byte-for-byte from before — **only the wrapping changes.**

### 2. `code` derivation rule (mechanical, not hand-maintained)

For a `SynapseError` subclass: **strip a trailing `"Error"` suffix, then convert CamelCase
to snake_case and lowercase.** One dictionary override exists (the base class only).

| Subclass                     | Status | `code`                    |
|------------------------------|--------|---------------------------|
| `BadRequestError`            | 400    | `bad_request`             |
| `AuthenticationError`        | 401    | `authentication`          |
| `ForbiddenError`             | 403    | `forbidden`               |
| `NotFoundError`              | 404    | `not_found`               |
| `ConflictError`              | 409    | `conflict`                |
| `GoneError`                  | 410    | `gone`                    |
| `PayloadTooLargeError`       | 413    | `payload_too_large`       |
| `UnsupportedMediaTypeError`  | 415    | `unsupported_media_type`  |
| `ValidationError`            | 422    | `validation`              |
| `NotImplementedFeatureError` | 501    | `not_implemented_feature` |
| `UpstreamError`              | 502    | `upstream`                |
| `ServiceUnavailableError`    | 503    | `service_unavailable`     |
| `SynapseError` (base, 500)   | 500    | `internal_error` (override) |

The rule is applied by `error_code_for()` in `app/errors.py`; adding a subclass yields a code
with **no per-class hand maintenance**. The only override is the base class (mechanical
`synapse` → the meaningful `internal_error`).

### 3. Three handlers, one envelope

`register_exception_handlers(app)` registers all three:

1. **`SynapseError`** → `synapse_error_handler`. `code` from the derivation rule; `message`
   from `exc.detail` (string); `details` = `exc.detail` when it is a non-string structured
   payload, else `null`; `status` = `exc.status_code`; headers preserved.
2. **`RequestValidationError`** (Pydantic 422s that do **not** go through `SynapseError`) →
   `request_validation_error_handler`. `code = "validation_error"` (deliberately distinct from
   the domain `ValidationError`'s `validation` — different source: framework schema mismatch vs
   business rule). `details` = a cleaned field-error list `[{loc, msg, type}]` (FastAPI's
   `input`/`url` fields are dropped to avoid echoing request content). `message` = a concise
   `"<loc>: <msg>; ..."` join so JSON-only consumers still get a readable summary.
3. **`HTTPException`** (Starlette base, catches every raw `raise HTTPException` still in the
   codebase) → `http_exception_handler_envelope`. `code` derived from the **status code** via a
   reverse map built from the subclass table (+ a few extras: `429 → rate_limited`,
   `500 → internal_error`, unknown → `http_<status>`). `message` from `exc.detail`.

### 4. Why the HTTPException fallback (and not migrating all 190 call sites)

There are ~190 `raise HTTPException(...)` sites across `backend/app/`. Migrating each to the
matching `SynapseError` subclass is the ideal end state (one source of truth) but is a large,
independent refactor with real regression surface, out of scope for this contract change. The
**fallback `HTTPException` handler gives every one of those sites the new envelope immediately**
with a status-derived `code` — so the wire contract is uniform *now*, and incremental migration
of call sites to `SynapseError` (which upgrades a status-derived code to a semantic one) can
happen later with zero further wire change. This is the pragmatic, invariant-safe choice.

### 5. Frontend consumption

`ApiError` gains a `code?: string` field so callers can branch on stable codes instead of
parsing messages. `errors.ts` exports `errorMessageFromBody()` / `errorCodeFromBody()` /
`parseErrorEnvelope()`; `checkResponse()` and every inline-parsing client are updated to read
`body.error.message` / `body.error.code`. `providerClient.formatDetail` (which existed solely
to flatten the old 422 *array* shape) is removed — the backend now emits a pre-joined `message`
for 422s, so no client-side array flattening is needed.

### 6. MCP surface — intentionally unchanged

MCP tool calls in `backend/app/mcp/server.py` do **not** flow through the HTTP exception path.
They already return protocol-level structured error payloads (`{"error": "<message>"}`
dictionaries) as tool results, per the FastMCP/`mcp` SDK's own error-reporting mechanism. These
are adequate and are consumed by the CLI agent as tool output, not as HTTP responses. Bolting
the HTTP envelope onto them would be a redundant second wrapper. **MCP error semantics are left
as-is** — noted here (not silently skipped) per the design brief. If a future need arises to
carry the same `code` taxonomy into MCP tool results, that is a separate, additive decision.

## Consequences

- **Breaking:** every HTTP error body changes from `{"detail": ...}` to
  `{"error": {code, message, status, details}}`. All Synapse-owned consumers (web, iOS,
  clipper) are updated in this change; external API/MCP-over-HTTP consumers must update.
- `code` is now a public contract: values in the table above are frozen. Renaming a
  `SynapseError` subclass would change its code — that is itself a breaking change and must be
  treated as one.
- 422 responses are now uniformly structured (`details` carries the field list) regardless of
  whether they originate from a domain `ValidationError` or Pydantic — but they carry different
  `code`s (`validation` vs `validation_error`) so consumers can tell the source apart.
- `docs/api/openapi.json` is regenerated (I8).
- The 1.9.2 "byte-for-bit identical to HTTPException" contract in `test_errors.py` is
  intentionally retired; the comparison-control-route tests are re-pointed to assert the new
  envelope (the plain-`HTTPException` control route now asserts it *also* gets the envelope via
  the fallback handler, which is the new invariant worth locking down).
- Migrating the ~190 raw `HTTPException` sites to `SynapseError` remains desirable follow-up
  (upgrades status-derived codes to semantic ones) but is explicitly **not** required by this
  contract and can proceed incrementally.
