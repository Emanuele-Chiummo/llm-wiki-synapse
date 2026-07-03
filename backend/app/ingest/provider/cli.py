"""
CliAgentProvider — CLI backend (F17, Karpathy lineage). Delegates the WHOLE ingest to a
claude-agent-sdk agent that runs its own bounded agent loop, reading/writing vault/wiki/ ONLY
through the Synapse MCP tools (ADR-0010) so I1/I5 hold identically to the orchestrated path.

Invariants:
  - I6: model id from ProviderSettings (provider_config), NEVER hardcoded; API key from the
    environment inside THIS module only (§12). Routing reaches this class via capabilities()
    (supports_agentic_loop=True), never via isinstance/type.
  - Auth (§12, ADR-0008 §3, amended by ADR-0043): the CLI backend resolves ONE of two auth modes
    via four signals, in precedence order. HIGHEST is a DB-set Claude subscription OAuth token that
    reaches this module ONLY on ProviderSettings.subscription_token (UI-settable per ADR-0043; the
    provider package stays ORM-free — no DB import here). When that token is non-empty we run in
    mode "subscription" AND inject it into the spawned `claude` CLI env as CLAUDE_CODE_OAUTH_TOKEN
    while SCRUBBING ANTHROPIC_API_KEY from that child env — the scrub is the safety crux (ADR-0043
    §2.3): Claude Code's own precedence ranks ANTHROPIC_API_KEY above the subscription, so a stray
    inherited API key would out-bill the deliberate UI action unless removed. Below the DB tier the
    ADR-0042 env signals are unchanged: ANTHROPIC_API_KEY set (non-empty) → "api-key" (billed);
    else CLAUDE_CODE_OAUTH_TOKEN set (non-empty) → "subscription"; else CLAUDE_CODE_USE_SUBSCRIPTION
    truthy → "subscription". For the env tiers we only detect PRESENCE — the token VALUE is never
    read or forwarded; the SDK's spawned CLI inherits it from the ambient process environment (no
    injection, no scrub — tier "api-key" by definition means the operator chose the API key).
    With none set we fail fast (ValueError) BEFORE any stream/loop opens (Do-NOT #9), naming all
    three env options. We NEVER PERMANENTLY mutate os.environ — an empty ANTHROPIC_API_KEY would
    silently override the subscription and bill per token, so it is treated as "unset". The DB-token
    scrub is applied via a SCOPED, restored-in-`finally` os.environ override around the SDK session
    (see _cli_subscription_env_override): the installed claude-agent-sdk (>=0.2,<0.3) merges
    ClaudeAgentOptions.env OVER the inherited os.environ (add/override only — it cannot DELETE an
    inherited key), so options.env alone cannot remove an inherited ANTHROPIC_API_KEY; the scoped
    os.environ override is the only way to make the key absent at the child-spawn snapshot, and it
    restores the parent env exactly (including keys that were absent) on exit or exception.
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
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ingest.provider._common import CAPTION_INSTRUCTION, resolve_image_bytes_and_media_type
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
# Container-friendly long-lived subscription OAuth token, produced on the host by
# `claude setup-token` and passed into the container as env (§12, ADR-0008 §3). The SDK's spawned
# `claude` CLI reads it from the inherited process environment; the provider only detects its
# PRESENCE to pass the auth gate — it NEVER reads or forwards the token value.
_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"  # noqa: S105 — env-var NAME, not a secret value
# Opt-in flag to drive the CLI backend with a logged-in Claude Code (Pro/Max) subscription
# session (ambient host login / mounted creds) instead of a pay-per-token API key. Truthy accepts
# 1/true/yes/on (case-insensitive). See _resolve_cli_auth_mode.
_SUBSCRIPTION_ENV = "CLAUDE_CODE_USE_SUBSCRIPTION"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
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


def _resolve_cli_auth_mode(subscription_token: str | None) -> str:
    """
    Resolve the CLI backend's auth mode, fail-fast (§12, ADR-0008 §3, amended by ADR-0043 §2.3).

    Precedence (never PERMANENTLY mutates os.environ; env tiers read PRESENCE only, no VALUE):
      1. subscription_token non-empty (DB, from the UI) → "subscription"  ← ADR-0043, HIGHEST.
         A DB-set token is an explicit operator action ("use my subscription for the CLI"); the
         caller injects it into the spawned CLI env AND scrubs ANTHROPIC_API_KEY so it
         deterministically wins (§2.3 crux). This tier OUTRANKS an env ANTHROPIC_API_KEY on purpose.
      2. ANTHROPIC_API_KEY env set & non-empty          → "api-key"      (billed per token; real
         cost — unchanged from ADR-0042)
      3. CLAUDE_CODE_OAUTH_TOKEN env set & non-empty     → "subscription" (container-friendly
         long-lived token from `claude setup-token`; NO API key; $0 marginal cost per ADR-0009)
      4. CLAUDE_CODE_USE_SUBSCRIPTION truthy             → "subscription" (ambient host login /
         mounted creds; same $0 convention)
      5. none                                            → raise ValueError naming ALL THREE env
         options (unchanged — the DB token is a UI setting, not an env option to name here)

    Empty-string = unset at EVERY tier (ADR-0042 rule preserved, ADR-0043 §2.3): an empty
    subscription_token or an empty ANTHROPIC_API_KEY is treated as "unset" so neither can silently
    outrank the subscription. Called BEFORE any SDK stream/loop opens so a misconfiguration surfaces
    as a clean pre-stream/pre-loop error, never a fake/half-open stream (Do-NOT #9).
    """
    if subscription_token:  # non-empty DB token (from the UI) — ADR-0043 tier 1, DB wins over env
        return "subscription"
    if os.environ.get(_ANTHROPIC_KEY_ENV):  # non-empty
        return "api-key"
    if os.environ.get(_OAUTH_TOKEN_ENV):  # non-empty container-friendly OAuth token
        return "subscription"
    if os.environ.get(_SUBSCRIPTION_ENV, "").strip().lower() in _TRUTHY:
        return "subscription"
    raise ValueError(
        "CLI provider auth not configured (§12, ADR-0008): set ANTHROPIC_API_KEY to bill per "
        "token, OR (subscription, $0 marginal cost) set CLAUDE_CODE_OAUTH_TOKEN — produced by "
        "`claude setup-token` on the host, container-friendly — OR set "
        "CLAUDE_CODE_USE_SUBSCRIPTION=true after logging in with `claude` (Claude Code) on the "
        "host."
    )


def _build_cli_child_env(base: dict[str, str], subscription_token: str) -> dict[str, str]:
    """
    Build the child-process environment for the spawned `claude` CLI on the DB-token path
    (ADR-0043 §2.3): a copy of *base* PLUS CLAUDE_CODE_OAUTH_TOKEN=<subscription_token> MINUS
    ANTHROPIC_API_KEY (scrubbed so the injected subscription deterministically wins). Pure — takes
    and returns a plain dict, mutates nothing; the scoped context manager below applies the result
    to os.environ around the SDK session. Kept as a standalone function so the scrub is
    unit-testable without opening any SDK stream.
    """
    child = dict(base)
    child[_OAUTH_TOKEN_ENV] = subscription_token
    child.pop(_ANTHROPIC_KEY_ENV, None)  # remove — an empty string would still out-bill (§2.3)
    return child


@contextmanager
def _cli_subscription_env_override(subscription_token: str) -> Iterator[None]:
    """
    Scoped os.environ override for the DB-token subscription path (ADR-0043 §2.4 fallback, which is
    the REQUIRED path with the installed SDK — see note below). For the duration of the wrapped SDK
    session it sets CLAUDE_CODE_OAUTH_TOKEN=<subscription_token> and REMOVES ANTHROPIC_API_KEY from
    os.environ, then restores the PREVIOUS os.environ state EXACTLY in `finally` — including keys
    that were absent before (they are removed again) — even on exception.

    Why mutate os.environ at all (documented trade-off): claude-agent-sdk (>=0.2,<0.3) builds the
    spawned CLI's environment as `{**os.environ, ..., **ClaudeAgentOptions.env}` — options.env is
    MERGED OVER the inherited process env and can only ADD or OVERRIDE keys, never DELETE an
    inherited one. So options.env alone cannot remove an inherited ANTHROPIC_API_KEY (the §2.3
    scrub). The SDK snapshots os.environ at child-spawn time, so scoping the mutation around the
    `ClaudeSDKClient(...)` session (and restoring it immediately after) is the only way to make the
    key ABSENT from the child while NEVER PERMANENTLY mutating the parent env (Do-NOT #3). The
    override is exception-safe: the `finally` restores the exact prior state regardless of outcome.
    """
    sentinel = object()
    prev_oauth: Any = os.environ.get(_OAUTH_TOKEN_ENV, sentinel)
    prev_api_key: Any = os.environ.get(_ANTHROPIC_KEY_ENV, sentinel)
    os.environ[_OAUTH_TOKEN_ENV] = subscription_token
    os.environ.pop(_ANTHROPIC_KEY_ENV, None)  # scrub: the safety crux (ADR-0043 §2.3)
    try:
        yield
    finally:
        # Restore EXACTLY — including re-removing keys that were absent before the override.
        if prev_oauth is sentinel:
            os.environ.pop(_OAUTH_TOKEN_ENV, None)
        else:
            os.environ[_OAUTH_TOKEN_ENV] = prev_oauth
        if prev_api_key is sentinel:
            os.environ.pop(_ANTHROPIC_KEY_ENV, None)
        else:
            os.environ[_ANTHROPIC_KEY_ENV] = prev_api_key


@contextmanager
def _cli_subscription_env_scope(subscription_token: str | None) -> Iterator[None]:
    """
    Call-site convenience wrapper (ADR-0043 §2.3): apply the inject+scrub os.environ override ONLY
    when a DB subscription token is present (non-empty). For env-sourced subscription (signals 3/4)
    and api-key mode the token is None/empty and this is a NO-OP — the SDK session inherits the
    ambient env unchanged (current behaviour). Lets both SDK call sites (delegate_ingest and
    _chat_stream) wrap their session unconditionally without branching.
    """
    if subscription_token:
        with _cli_subscription_env_override(subscription_token):
            yield
    else:
        yield


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
            supports_vision=True,  # R8-2: Claude via the CLI reads image files (F12)
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

        With NEITHER ANTHROPIC_API_KEY nor CLAUDE_CODE_USE_SUBSCRIPTION this raises a CLEAN
        pre-stream config error (ValueError) BEFORE returning the generator — never a fake stream
        (Do-NOT #9). Because this is a coroutine (awaited by run_chat_stream before iteration), the
        raise surfaces as a normal provider error event, not a half-open stream. Dev default stays
        Ollama.
        """
        # ADR-0043 §2.3: a DB-set token (on ProviderSettings.subscription_token) outranks env.
        auth_mode = _resolve_cli_auth_mode(self._config.subscription_token)

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
            ClaudeAgentOptions, ClaudeSDKClient, messages, retrieval_context, max_turns, auth_mode
        )

    async def _chat_stream(
        self,
        options_cls: Any,
        client_cls: Any,
        messages: list[Message],
        retrieval_context: str,
        max_turns: int,
        auth_mode: str,
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
        # ADR-0043 §2.3: when the subscription is driven by the DB token (not env), inject it into
        # the spawned CLI env and scrub ANTHROPIC_API_KEY. The scoped override wraps ONLY the SDK
        # session and restores os.environ in its own finally — parent env is never permanently
        # mutated (Do-NOT #3). Env-sourced subscription / api-key inherit the ambient env unchanged.
        db_token = self._config.subscription_token
        try:
            with _cli_subscription_env_scope(db_token):
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
            self._record_usage(_finalize_chat_usage(usage, sdk_cost_usd, auth_mode))

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
            mcp_server:    the in-process SDK MCP server config (McpSdkServerConfig dict from
                           create_sdk_mcp_server) exposing MCP_TOOL_NAMES, built by
                           app.mcp.server.build_sdk_mcp_server(). NOT a FastMCP object — the SDK
                           would try to JSON-serialize that as an external server config and crash.
                           See the INTEGRATION SEAM.

        Returns DelegatedIngestResult. Records Usage (real SDK cost under "api-key" auth, else
        $0.00 by the subscription/OAuth convention, ADR-0009). Raises a clean pre-loop ValueError
        if NEITHER ANTHROPIC_API_KEY nor CLAUDE_CODE_USE_SUBSCRIPTION is set (Do-NOT #9).
        """
        # ADR-0043 §2.3: a DB-set token (on ProviderSettings.subscription_token) outranks env.
        auth_mode = _resolve_cli_auth_mode(self._config.subscription_token)

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
        #   (I5) and incremental upsert (I1) run on every write. The orchestrator builds the
        #   in-process SDK MCP server via app.mcp.server.build_sdk_mcp_server() (a
        #   McpSdkServerConfig dict, NOT a FastMCP object) and passes it in as `mcp_server`.
        #   A None server raises here rather than letting the agent fall back to raw filesystem
        #   writes (which would bypass I1/I5).
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
            # In-process SDK MCP tools are namespaced by the SDK as mcp__<server>__<tool>, so the
            # model must be granted the NAMESPACED names (bare names would never match). ADR-0010.
            allowed_tools=[f"mcp__synapse__{n}" for n in MCP_TOOL_NAMES],
            mcp_servers={"synapse": mcp_server},  # McpSdkServerConfig dict (create_sdk_mcp_server)
        )

        pages_written = 0
        usage = Usage(input_tokens=0, output_tokens=0, total_cost_usd=0.0)
        # Cost the SDK reports on its terminal ResultMessage (None until we see it).
        sdk_cost_usd: float | None = None
        converged = False

        # ADR-0043 §2.3: DB-token subscription → inject CLAUDE_CODE_OAUTH_TOKEN + scrub
        # ANTHROPIC_API_KEY from the child env for the duration of the SDK session, restored after
        # (no-op for env-sourced subscription / api-key). Parent os.environ never permanently
        # mutated (Do-NOT #3).
        with _cli_subscription_env_scope(self._config.subscription_token):
            async with ClaudeSDKClient(options=options) as client:
                await client.query(
                    "Ingest the following source into the wiki. Classify it, create schema-valid "
                    "pages via write_page, assign each page 3-6 concise, lowercase, reusable "
                    "frontmatter `tags` for navigation, and link related pages. Source:\n\n"
                    + source_text
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
            if auth_mode == "subscription":
                # Expected & intended: subscription (Pro/Max OAuth) has $0 marginal cost.
                logger.info(
                    "CliAgentProvider: subscription auth (CLAUDE_CODE_USE_SUBSCRIPTION) — "
                    "recording total_cost_usd=$0.00 as intended (ADR-0009 convention)"
                )
            else:
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

    # ── Vision (R8-2 / F12) ──────────────────────────────────────────────────────

    async def caption_image(self, path_or_bytes: str | Path | bytes, context: str) -> str:
        """
        Caption an image via a bounded, read-only claude-agent-sdk session (R8-2). The agent is
        granted ONLY the built-in Read tool scoped to the image's directory (no write_page /
        fs-write), reads the image file, and returns a plain-text caption. Bytes inputs are written
        to a scoped temp file so the agent's Read tool can access them; the temp file is removed in
        a finally. Usage recorded out of band (real SDK cost under api-key auth, else $0 by the
        subscription convention, ADR-0009 / I7). Raises a clean pre-session ValueError on missing
        auth (Do-NOT #9).
        """
        auth_mode = _resolve_cli_auth_mode(self._config.subscription_token)

        try:
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        except ImportError as exc:  # pragma: no cover - exercised only without the SDK installed
            raise RuntimeError(
                "claude-agent-sdk is not installed; the CLI provider requires it (R3)."
            ) from exc

        # Resolve to a concrete filesystem path the agent's Read tool can open.
        tmp_path: Path | None = None
        if isinstance(path_or_bytes, (str, Path)):
            image_path = Path(path_or_bytes)
        else:
            data, media_type = resolve_image_bytes_and_media_type(path_or_bytes)
            suffix = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/gif": ".gif",
                "image/webp": ".webp",
            }.get(media_type, ".png")
            fd, name = tempfile.mkstemp(suffix=suffix, prefix="synapse_caption_")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            tmp_path = Path(name)
            image_path = tmp_path

        prompt = (
            f"{context}\n\n{CAPTION_INSTRUCTION}\n\nRead the image file at: {image_path}"
            if context.strip()
            else f"{CAPTION_INSTRUCTION}\n\nRead the image file at: {image_path}"
        )
        options = ClaudeAgentOptions(
            model=self._model,  # from provider_config (I6)
            permission_mode="acceptEdits",  # non-interactive (CLAUDE.md §5)
            cwd=str(image_path.parent),  # scope filesystem access to the image's directory
            allowed_tools=["Read"],  # read-only: no write_page / fs-write (R8-2)
            max_turns=_chat_agent_max_turns(),  # bounded agent turns (I7)
        )

        parts: list[str] = []
        usage = Usage(input_tokens=0, output_tokens=0, total_cost_usd=0.0)
        sdk_cost_usd: float | None = None
        try:
            with _cli_subscription_env_scope(self._config.subscription_token):
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(prompt)
                    async for message in client.receive_response():
                        parts.extend(_extract_text_deltas(message))
                        usage = _merge_sdk_usage(usage, message)
                        msg_cost = _extract_sdk_cost(message)
                        if msg_cost is not None:
                            sdk_cost_usd = msg_cost
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        self._record_usage(_finalize_chat_usage(usage, sdk_cost_usd, auth_mode))
        caption = "".join(parts).strip()
        if not caption:
            raise ValueError("CliAgentProvider vision returned an empty caption")
        return caption


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


def _finalize_chat_usage(usage: Usage, sdk_cost_usd: float | None, auth_mode: str) -> Usage:
    """
    Apply the NB-4 cost convention (ADR-0009) to a chat run's accumulated Usage:
      - SDK-reported cost present and > 0 (API-key billing) → record it truthfully;
      - otherwise (subscription/OAuth, or SDK exposed none) → total_cost_usd = 0.00. When
        auth_mode == "subscription" the $0 is expected & intended (INFO); otherwise it is logged
        as a WARNING (an anomaly worth noticing).
    Token counts are carried through; a WARNING is logged if the SDK exposed none. Never raises.
    """
    if usage.input_tokens == 0 and usage.output_tokens == 0:
        logger.warning("CliAgentProvider.chat: SDK exposed no token counts — recording tokens=0")
    if sdk_cost_usd is not None and sdk_cost_usd > 0.0:
        total_cost_usd = sdk_cost_usd
    elif auth_mode == "subscription":
        total_cost_usd = 0.0
        logger.info(
            "CliAgentProvider.chat: subscription auth (CLAUDE_CODE_USE_SUBSCRIPTION) — "
            "recording total_cost_usd=$0.00 as intended (ADR-0009 convention)"
        )
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
    """
    Best-effort count of write_page tool invocations in an SDK message (for pages_written).

    In-process SDK MCP tools are namespaced by the SDK, so the tool-use block name is
    ``mcp__synapse__write_page`` — not the bare ``write_page``. Match both (endswith is
    sufficient: it accepts the bare name AND the namespaced form, ADR-0010).
    """
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return 0
    count = 0
    for block in content:
        name = getattr(block, "name", None)
        if isinstance(name, str) and (name == "write_page" or name.endswith("__write_page")):
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
    "_build_cli_child_env",
    "_cli_subscription_env_override",
    "_cli_subscription_env_scope",
    "_resolve_cli_auth_mode",
]
