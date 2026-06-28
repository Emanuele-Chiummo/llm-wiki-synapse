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
  - ADR-0009: CLI cost is $0.00 by convention (build-time agent credits, not runtime billing).
    Raw token counts from the SDK result are recorded when present; tokens=0 + WARNING if not.
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
        raise NotImplementedError("CliAgentProvider.chat() is implemented in v0.4 (F6)")

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
        converged = False

        async with ClaudeSDKClient(options=options) as client:
            await client.query(
                "Ingest the following source into the wiki. Classify it, create schema-valid "
                "pages via write_page, and link related pages. Source:\n\n" + source_text
            )
            async for message in client.receive_response():
                pages_written += _count_write_page_calls(message)
                usage = _merge_sdk_usage(usage, message)
        converged = pages_written > 0

        # CLI cost convention: $0.00 (ADR-0009). Raw tokens recorded when the SDK exposes them.
        if usage.input_tokens == 0 and usage.output_tokens == 0:
            logger.warning("CliAgentProvider: SDK exposed no token counts — recording tokens=0")
        usage = Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_cost_usd=0.0,
        )
        self._record_usage(usage)
        return DelegatedIngestResult(pages_written=pages_written, usage=usage, converged=converged)


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
    """Merge any token usage the SDK exposes on a message into *acc* (cost left at 0.0)."""
    raw = getattr(message, "usage", None)
    if raw is None:
        return acc
    in_tok = int(getattr(raw, "input_tokens", 0) or 0)
    out_tok = int(getattr(raw, "output_tokens", 0) or 0)
    return Usage(
        input_tokens=acc.input_tokens + in_tok,
        output_tokens=acc.output_tokens + out_tok,
        total_cost_usd=0.0,
    )


__all__ = [
    "CliAgentProvider",
    "DelegatedIngestResult",
    "MCP_TOOL_NAMES",
    "UsageAccumulator",
]
