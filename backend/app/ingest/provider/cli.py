"""
CliAgentProvider — CLI backend (F17, Karpathy lineage). Delegates the WHOLE ingest to a
claude-agent-sdk agent that runs its own bounded agent loop, reading/writing vault/wiki/ ONLY
through the Synapse MCP tools (ADR-0010) so I1/I5 hold identically to the orchestrated path.

Invariants:
  - I6: model id from ProviderSettings (provider_config), NEVER hardcoded; API key from the
    environment inside THIS module only (§12). Routing reaches this class via capabilities()
    (supports_agentic_loop=True), never via isinstance/type.
  - I7: the SDK is given a token_budget (ADR-0009: 100k default for CLI); the provider aborts
    if the SDK reports the budget exceeded. The orchestrator records ONE ingest_runs row.
  - ADR-0009 (as amended by NB-4): record the REAL cost the SDK reports. claude-agent-sdk
    surfaces a `total_cost_usd` on its terminal ResultMessage when the run was billed via an
    API key. We use that value when it is present and > 0; we fall back to the historical
    $0.00 convention (with a WARNING) ONLY when the SDK reports no cost — i.e. subscription /
    OAuth auth, whose marginal cost genuinely is $0. Raw token counts are recorded when present;
    tokens=0 + WARNING if not. The Usage normalization contract is unchanged (input/output
    tokens + total_cost_usd); only total_cost_usd is now truthful.
  - ADR-0010: the agent uses MCP tools named search_wiki / write_page / get_page / list_pages.

The claude-agent-sdk is imported LAZILY so the rest of the package (and the infra-free unit
tests) does not require the SDK to be installed. The actual SDK<->MCP wiring lives behind the
`delegate_ingest()` seam below — see the MCP INTEGRATION SEAM block.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.provider.config import ProviderSettings
from app.ingest.schemas import (
    Analysis,
    Message,
    ProviderCapabilities,
    Usage,
    WikiPage,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
_DEFAULT_MAX_CONTEXT = 200_000

# I7 third bound for CLI chat (ADR-0022 §2.7 / AQ-v0.5-7): caps the SDK agent loop turns.
# Env-sourced, never a literal in app code; the other two bounds (token_budget + timeout_seconds)
# flow through run_chat_stream and need no provider_config change.
_CHAT_AGENT_MAX_TURNS_ENV = "CHAT_AGENT_MAX_TURNS"
_DEFAULT_CHAT_AGENT_MAX_TURNS = 8

# The four MCP tool names the delegated agent is granted (ADR-0010 §"MCP tool contracts").
# These are NAMES ONLY; the FastMCP server object that implements them is owned by
# backend-engineer (backend/app/mcp/server.py). cli.py references them by name and receives
# the constructed server via the `mcp_server` integration seam (see delegate_ingest).
MCP_TOOL_NAMES: tuple[str, ...] = ("search_wiki", "write_page", "get_page", "list_pages")


@dataclass
class DelegatedIngestResult:
    """Outcome of a CLI delegated ingest run (consumed by the orchestrator)."""

    pages_written: int
    usage: Usage
    converged: bool


class CliAgentProvider(InferenceProvider):
    """
    claude-agent-sdk delegated provider. The orchestrator routes here when
    capabilities().supports_agentic_loop is True and calls `delegate_ingest()` (NOT the
    analyze/generate loop — those raise to make accidental orchestrated use loud).
    """

    def __init__(self, config: ProviderSettings) -> None:
        self._config = config
        self._model = config.model_id  # from provider_config — never hardcoded (I6)
        self._token_budget = config.token_budget

    # ── Capabilities ─────────────────────────────────────────────────────────────

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="cli",
            supports_tools=True,
            supports_agentic_loop=True,  # → delegated route (ADR-0007 §3)
            max_context=_DEFAULT_MAX_CONTEXT,
            name="CliAgentProvider",
        )

    # ── Orchestrated-loop methods: not used on the delegated path ───────────────

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        raise RuntimeError(
            "CliAgentProvider runs the delegated path (delegate_ingest); analyze() is not "
            "called for an agentic provider (route via capabilities(), ADR-0007 §3)."
        )

    async def generate(self, analysis: Analysis, retrieval_context: str) -> list[WikiPage]:
        raise RuntimeError(
            "CliAgentProvider runs the delegated path (delegate_ingest); generate() is not "
            "called for an agentic provider (route via capabilities(), ADR-0007 §3)."
        )

    async def chat(self, messages: list[Message], retrieval_context: str) -> AsyncIterator[str]:
        """
        Delegated streaming chat via claude-agent-sdk (F17, ADR-0022 §2.7). Mirrors
        delegate_ingest's SDK usage but READ-ONLY: the agent is granted NO write_page /
        filesystem-write tools (this is chat, not ingest). `retrieval_context` (the light system
        header + retrieved context, built by run_chat_stream) is injected as the system prompt.

        Yields raw text deltas verbatim — the SAME shape OllamaProvider / ApiProvider use, so
        chat/stream.py needs no special-casing (it awaits this coroutine, then iterates the
        returned async generator; the <think> scanner + token_budget/timeout bounds run there).

        Bounded by THREE caps (I7, ADR-0022 §2.7 / Do-NOT #8):
          1. token_budget    — enforced by run_chat_stream over the yielded deltas (existing);
          2. timeout_seconds — enforced by run_chat_stream around consumption (existing);
          3. CHAT_AGENT_MAX_TURNS (env, default 8) — passed to the SDK as max_turns here.

        With no ANTHROPIC_API_KEY this raises a CLEAN pre-stream config error (ValueError) BEFORE
        returning the generator — never a fake stream (Do-NOT #9). Because this is a coroutine
        (awaited by run_chat_stream before iteration), the raise surfaces as a normal provider
        error event, not a half-open stream. Dev default stays Ollama.
        """
        api_key = os.environ.get(_ANTHROPIC_KEY_ENV)
        if not api_key:
            raise ValueError(f"{_ANTHROPIC_KEY_ENV} not set in environment (§12, ADR-0008)")

        # Lazy SDK import here too, so a missing SDK is a clean pre-stream error (not mid-stream).
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        except ImportError as exc:  # pragma: no cover - exercised only without the SDK installed
            raise RuntimeError(
                "claude-agent-sdk is not installed; the CLI provider requires it (R3). "
                "Install it on the host running CLI chat."
            ) from exc

        max_turns = _chat_agent_max_turns()
        return self._chat_stream(
            ClaudeAgentOptions, ClaudeSDKClient, messages, retrieval_context, max_turns
        )

    async def _chat_stream(
        self,
        options_cls: Any,
        client_cls: Any,
        messages: list[Message],
        retrieval_context: str,
        max_turns: int,
    ) -> AsyncIterator[str]:
        """
        The actual SDK streaming session. Read-only: no write_page / filesystem-write tools are
        granted (allowed_tools=[]), so the agent can only answer from `retrieval_context`.
        Records Usage out of band (cost per NB-4) in a finally block, even on early aclose()
        (token_budget cap) or mid-stream error — keeping the I7 ledger truthful.
        """
        options = options_cls(
            model=self._model,  # from provider_config (I6)
            system_prompt=retrieval_context,  # light header + retrieved context (ADR-0022 §2.7)
            permission_mode="acceptEdits",  # non-interactive (CLAUDE.md §5)
            allowed_tools=[],  # READ-ONLY chat: no write_page / fs-write (ADR-0022 §2.7)
            max_turns=max_turns,  # third I7 bound (CHAT_AGENT_MAX_TURNS)
        )

        usage = Usage(input_tokens=0, output_tokens=0, total_cost_usd=0.0)
        sdk_cost_usd: float | None = None
        try:
            async with client_cls(options=options) as client:
                await client.query(_build_chat_prompt(messages))
                async for message in client.receive_response():
                    for delta in _extract_text_deltas(message):
                        if delta:
                            yield delta
                    usage = _merge_sdk_usage(usage, message)
                    msg_cost = _extract_sdk_cost(message)
                    if msg_cost is not None:
                        sdk_cost_usd = msg_cost
        finally:
            self._record_usage(_finalize_chat_usage(usage, sdk_cost_usd))

    # ── Delegated ingest (the agentic route) ────────────────────────────────────

    async def delegate_ingest(
        self,
        *,
        source_text: str,
        system_prompt: str,
        vault_dir: str,
        mcp_server: Any | None = None,
    ) -> DelegatedIngestResult:
        """
        Run the full ingest as a claude-agent-sdk agent loop (delegated route).

        Args:
            source_text:   the raw source document to ingest.
            system_prompt: schema.md + purpose.md content (F2/F3) — built by the orchestrator.
            vault_dir:     absolute path to the vault root; filesystem tools are scoped here.
            mcp_server:    the FastMCP server object exposing MCP_TOOL_NAMES (built by
                           backend-engineer in app/mcp/server.py). See the INTEGRATION SEAM.

        Returns DelegatedIngestResult. Records Usage (cost $0.00 by convention, ADR-0009).
        """
        api_key = os.environ.get(_ANTHROPIC_KEY_ENV)
        if not api_key:
            raise ValueError(f"{_ANTHROPIC_KEY_ENV} not set in environment (§12, ADR-0008)")

        # ── Lazy SDK import (keeps the package import-clean without the SDK installed) ──
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        except ImportError as exc:  # pragma: no cover - exercised only with SDK installed
            raise RuntimeError(
                "claude-agent-sdk is not installed; the CLI provider requires it (R3). "
                "Install it on the host running delegated ingest."
            ) from exc

        # ─────────────────────────────────────────────────────────────────────────
        # MCP INTEGRATION SEAM (cli.py <-> app/mcp/server.py — backend-engineer owns server)
        #   The agent must read/write vault/wiki/ ONLY via the in-process Synapse MCP tools
        #   (search_wiki/write_page/get_page/list_pages, ADR-0010) so frontmatter validation
        #   (I5) and incremental upsert (I1) run on every write. backend-engineer builds the
        #   FastMCP server object and passes it in as `mcp_server`. Until that lands, a None
        #   server raises here rather than letting the agent fall back to raw filesystem
        #   writes (which would bypass I1/I5).
        #   TODO(backend-engineer): construct the FastMCP server in app/mcp/server.py and wire
        #     it into ClaudeAgentOptions(mcp_servers=...). See ADR-0010 §2 (single write path).
        # ─────────────────────────────────────────────────────────────────────────
        if mcp_server is None:
            raise RuntimeError(
                "CliAgentProvider.delegate_ingest requires the Synapse MCP server "
                "(app/mcp/server.py) to enforce I1/I5 on writes (ADR-0010). "
                "Pass mcp_server=<FastMCP server>. See MCP INTEGRATION SEAM in cli.py."
            )

        options = ClaudeAgentOptions(
            model=self._model,  # from provider_config (I6)
            system_prompt=system_prompt,  # schema.md + purpose.md (F2/F3)
            permission_mode="acceptEdits",  # non-interactive (CLAUDE.md §5)
            cwd=vault_dir,  # filesystem tools scoped to the vault
            allowed_tools=list(MCP_TOOL_NAMES),
            mcp_servers={"synapse": mcp_server},
        )

        pages_written = 0
        usage = Usage(input_tokens=0, output_tokens=0, total_cost_usd=0.0)
        # Cost the SDK reports on its terminal ResultMessage (None until we see it).
        sdk_cost_usd: float | None = None
        converged = False

        async with ClaudeSDKClient(options=options) as client:
            await client.query(
                "Ingest the following source into the wiki. Classify it, create schema-valid "
                "pages via write_page, and link related pages. Source:\n\n" + source_text
            )
            async for message in client.receive_response():
                pages_written += _count_write_page_calls(message)
                usage = _merge_sdk_usage(usage, message)
                msg_cost = _extract_sdk_cost(message)
                if msg_cost is not None:
                    sdk_cost_usd = msg_cost
        converged = pages_written > 0

        # Raw tokens recorded when the SDK exposes them.
        if usage.input_tokens == 0 and usage.output_tokens == 0:
            logger.warning("CliAgentProvider: SDK exposed no token counts — recording tokens=0")

        # NB-4: use the SDK-reported cost when present & > 0 (API-key billing); else fall back to
        # the $0.00 convention (subscription/OAuth → marginal cost is genuinely $0).
        if sdk_cost_usd is not None and sdk_cost_usd > 0.0:
            total_cost_usd = sdk_cost_usd
        else:
            total_cost_usd = 0.0
            logger.warning(
                "CliAgentProvider: SDK reported no billable cost "
                "(subscription/OAuth auth or unavailable) — recording total_cost_usd=$0.00 "
                "by the build-time-credit convention (ADR-0009)"
            )

        usage = Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_cost_usd=total_cost_usd,
        )
        self._record_usage(usage)
        return DelegatedIngestResult(pages_written=pages_written, usage=usage, converged=converged)


# ── Chat helpers ────────────────────────────────────────────────────────────────


def _chat_agent_max_turns() -> int:
    """
    Read the CHAT_AGENT_MAX_TURNS env (third I7 bound for CLI chat, ADR-0022 §2.7 / AQ-v0.5-7).
    Defaults to 8. A non-positive or malformed value falls back to the default with a WARNING
    (a runaway loop is never allowed — I7).
    """
    raw = os.environ.get(_CHAT_AGENT_MAX_TURNS_ENV)
    if not raw:
        return _DEFAULT_CHAT_AGENT_MAX_TURNS
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not an int — using default %d (I7)",
            _CHAT_AGENT_MAX_TURNS_ENV,
            raw,
            _DEFAULT_CHAT_AGENT_MAX_TURNS,
        )
        return _DEFAULT_CHAT_AGENT_MAX_TURNS
    if value <= 0:
        logger.warning(
            "%s=%d is not positive — using default %d (I7)",
            _CHAT_AGENT_MAX_TURNS_ENV,
            value,
            _DEFAULT_CHAT_AGENT_MAX_TURNS,
        )
        return _DEFAULT_CHAT_AGENT_MAX_TURNS
    return value


def _build_chat_prompt(messages: list[Message]) -> str:
    """
    Render the conversation turns into a single prompt for the SDK agent. The grounding context
    travels via the system_prompt (ADR-0022 §2.7), so here we only carry the user/assistant turns
    (system turns are dropped — they belong to the system_prompt). Role-tagged so the agent reads
    the dialogue order; the latest user message is the question to answer.
    """
    lines = [f"{m.role}: {m.content}" for m in messages if m.role in ("user", "assistant")]
    return "\n\n".join(lines)


def _extract_text_deltas(message: Any) -> list[str]:
    """
    Best-effort extraction of assistant text from an SDK message, yielded verbatim as chat deltas
    (NO server-side parse — I3; the <think> scanner runs in chat/stream.py). Reads the documented
    `content` block list ({type:"text", text:...} or objects with a `.text` attr) and tolerates a
    bare `.text` / dict shape, because the SDK message shape may evolve (R3). Tool-use / result
    messages carry no assistant text and return [].
    """
    content = getattr(message, "content", None)
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if isinstance(text, str) and text:
                out.append(text)
        return out
    # Some SDK shapes expose a bare text attribute / dict.
    text = getattr(message, "text", None)
    if text is None and isinstance(message, dict):
        text = message.get("text")
    if isinstance(text, str) and text:
        return [text]
    return []


def _finalize_chat_usage(usage: Usage, sdk_cost_usd: float | None) -> Usage:
    """
    Apply the NB-4 cost convention (ADR-0009) to a chat run's accumulated Usage:
      - SDK-reported cost present and > 0 (API-key billing) → record it truthfully;
      - otherwise (subscription/OAuth, or SDK exposed none) → total_cost_usd = 0.00 + WARNING.
    Token counts are carried through; a WARNING is logged if the SDK exposed none. Never raises.
    """
    if usage.input_tokens == 0 and usage.output_tokens == 0:
        logger.warning("CliAgentProvider.chat: SDK exposed no token counts — recording tokens=0")
    if sdk_cost_usd is not None and sdk_cost_usd > 0.0:
        total_cost_usd = sdk_cost_usd
    else:
        total_cost_usd = 0.0
        logger.warning(
            "CliAgentProvider.chat: SDK reported no billable cost "
            "(subscription/OAuth auth or unavailable) — recording total_cost_usd=$0.00 "
            "by the build-time-credit convention (ADR-0009)"
        )
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_cost_usd=total_cost_usd,
    )


# ── SDK result helpers (defensive — SDK message shape may evolve) ───────────────


def _count_write_page_calls(message: Any) -> int:
    """Best-effort count of write_page tool invocations in an SDK message (for pages_written)."""
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return 0
    count = 0
    for block in content:
        name = getattr(block, "name", None)
        if name == "write_page":
            count += 1
    return count


def _merge_sdk_usage(acc: Usage, message: Any) -> Usage:
    """
    Merge any token usage the SDK exposes on a message into *acc*.

    Cost is intentionally left at 0.0 here — the per-run cost is taken from the terminal
    ResultMessage's `total_cost_usd` (see `_extract_sdk_cost`), not summed per message.
    """
    raw = getattr(message, "usage", None)
    if raw is None:
        return acc
    # The SDK may expose usage as an object (attrs) or a dict — read both shapes defensively.
    if isinstance(raw, dict):
        in_tok = int(raw.get("input_tokens", 0) or 0)
        out_tok = int(raw.get("output_tokens", 0) or 0)
    else:
        in_tok = int(getattr(raw, "input_tokens", 0) or 0)
        out_tok = int(getattr(raw, "output_tokens", 0) or 0)
    return Usage(
        input_tokens=acc.input_tokens + in_tok,
        output_tokens=acc.output_tokens + out_tok,
        total_cost_usd=0.0,
    )


def _extract_sdk_cost(message: Any) -> float | None:
    """
    Return the SDK-reported run cost from a message, or None if this message carries none.

    claude-agent-sdk emits a terminal `ResultMessage` (subtype `"result"`) that carries the
    cumulative `total_cost_usd: float | None` for the whole run when it was billed via an API
    key (it is None / 0 for subscription/OAuth auth, whose marginal cost is $0). Assistant /
    tool messages carry no such field, so they return None and the caller keeps the last seen
    value. Read defensively (getattr / dict) because the SDK message shape may evolve (R3);
    the documented attribute is `total_cost_usd` on the ResultMessage.
    """
    raw = getattr(message, "total_cost_usd", None)
    if raw is None and isinstance(message, dict):
        raw = message.get("total_cost_usd")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CliAgentProvider",
    "DelegatedIngestResult",
    "MCP_TOOL_NAMES",
    "UsageAccumulator",
]
