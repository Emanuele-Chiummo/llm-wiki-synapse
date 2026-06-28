"""
Streaming-safe <think>…</think> span splitter (F7, ADR-0019 §2.4).

A reasoning model wraps its chain-of-thought in `<think>…</think>`. We split it ON THE SERVER
during streaming with a tiny 2-state machine so the client NEVER parses tags (I3): text outside
the span becomes `{type:"token"}` events, text inside becomes `{type:"think"}` events.

Partial-tag safety (the load-bearing invariant): a tag can be split across two model chunks
(`...<thi` | `nk>...`). The scanner buffers a trailing fragment that COULD be the prefix of the
relevant tag (open `<think>` while OUTSIDE, close `</think>` while INSIDE) and does not classify
it until it can decide. We never emit a fragment that might still turn out to be (part of) a tag.

The split is TRANSPORT ONLY. The caller persists the RAW concatenation of all model deltas
(including the literal `<think>…</think>`) un-mutated (AC-F7-2 / Do-NOT #7); the same scan is
re-derivable at render time from the stored string.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

_OPEN = "<think>"
_CLOSE = "</think>"

# Longest tag we must guard a partial of. We buffer up to (len-1) trailing chars that could be
# the start of the next relevant tag so a tag split across chunks is never mis-emitted.
_MAX_TAG = max(len(_OPEN), len(_CLOSE))

EventKind = Literal["token", "think"]


@dataclass
class ThinkScanner:
    """
    Stateful 2-state (OUTSIDE / INSIDE) scanner over a stream of raw text chunks.

    Usage::

        scanner = ThinkScanner()
        for chunk in model_deltas:
            for kind, text in scanner.feed(chunk):
                emit(kind, text)          # kind ∈ {"token","think"}
        for kind, text in scanner.flush():
            emit(kind, text)              # drain any safely-held tail

    `feed()` yields (kind, text) pairs for everything it can SAFELY classify now; it holds back
    only a possible partial tag. `flush()` MUST be called once the upstream stream ends to emit
    the final held buffer (which by then cannot be a real tag).
    """

    inside: bool = False
    _buf: str = field(default="", init=False)

    # ── Public API ──────────────────────────────────────────────────────────────

    def feed(self, chunk: str) -> Iterator[tuple[EventKind, str]]:
        """Consume *chunk*; yield (kind, text) for everything safely classifiable now."""
        if not chunk:
            return
        self._buf += chunk
        yield from self._drain(final=False)

    def flush(self) -> Iterator[tuple[EventKind, str]]:
        """Emit any remaining buffered text at stream end (it can no longer be a real tag)."""
        yield from self._drain(final=True)

    # ── Core ────────────────────────────────────────────────────────────────────

    def _drain(self, *, final: bool) -> Iterator[tuple[EventKind, str]]:
        """
        Emit classified text out of `self._buf`, transitioning state on each tag boundary.

        While not `final`, we keep a trailing fragment in the buffer if it could be the prefix
        of the tag we are watching for (so a tag split across chunks is never mis-emitted).
        """
        while self._buf:
            tag = _OPEN if not self.inside else _CLOSE
            kind: EventKind = "token" if not self.inside else "think"

            idx = self._buf.find(tag)
            if idx != -1:
                # A full tag boundary is present: emit everything before it, consume the tag,
                # flip state, and continue scanning the remainder.
                before = self._buf[:idx]
                if before:
                    yield (kind, before)
                self._buf = self._buf[idx + len(tag) :]
                self.inside = not self.inside
                continue

            # No full tag in the buffer. Decide how much of the tail is safe to emit.
            if final:
                # Stream is over: nothing left can become a tag — emit it all.
                if self._buf:
                    yield (kind, self._buf)
                self._buf = ""
                return

            hold = _safe_hold_len(self._buf)
            if hold == 0:
                # No suffix could be a tag prefix — emit the whole buffer, wait for more input.
                yield (kind, self._buf)
                self._buf = ""
                return
            emit_upto = len(self._buf) - hold
            if emit_upto > 0:
                yield (kind, self._buf[:emit_upto])
                self._buf = self._buf[emit_upto:]
            # Keep `self._buf` (the held possible-partial-tag) and wait for more input.
            return


def _safe_hold_len(buf: str) -> int:
    """
    Return the length of the longest SUFFIX of *buf* that could be the start (prefix) of either
    `<think>` or `</think>`. That many trailing chars must be held back (could still become a
    tag once more input arrives). 0 means the whole buffer is safe to emit.
    """
    max_k = min(len(buf), _MAX_TAG - 1)
    for k in range(max_k, 0, -1):
        suffix = buf[-k:]
        if _OPEN.startswith(suffix) or _CLOSE.startswith(suffix):
            return k
    return 0


def split_think(raw: str) -> tuple[str, list[tuple[EventKind, str]]]:
    """
    Pure, whole-string re-derivation used at render/reload time (ADR-0019 §2.4): run the same
    scanner over a complete stored message and return its (visible_text, segments).

    `visible_text` is the concatenation of all "token" segments (the user-visible content);
    `segments` is the ordered (kind, text) list (think + token) for a richer renderer. This is
    a single pass over the immutable string — NOT per token (I3).
    """
    scanner = ThinkScanner()
    segments: list[tuple[EventKind, str]] = []
    for kind, text in scanner.feed(raw):
        segments.append((kind, text))
    for kind, text in scanner.flush():
        segments.append((kind, text))
    visible = "".join(text for kind, text in segments if kind == "token")
    return visible, segments
