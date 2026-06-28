"""Chat service module (F6/F7, M4 Phase 3, ADR-0019).

Houses the chat-only pieces the orchestrator/main wire together:
  - context.py  : light system context builder (purpose.md + overview.md, budget-capped §2.6).
  - think.py    : streaming-safe <think>…</think> span scanner (F7, §2.4).
  - stream.py   : bounded NDJSON event stream wrapper (token_budget + timeout, I7, §2.2).

These do NOT live in ingest/ because chat is a distinct operation routed by
resolve_provider_config("chat", ...) (I6). They reuse the InferenceProvider.chat() seam.
"""
