"""
Curated provider vendor catalog (W1 / F17) — the fixed one-row-per-vendor registry the
Settings UI renders (LLM Wiki "LLM Models" parity).

This is a *catalog*, not a routing surface: it advertises which vendors exist, the correct
OpenAI-compatible ``default_base_url`` for each, whether a UI API key is needed, and a list of
known ``model_presets`` for the model dropdown (the user may always type a custom id). The
actual backend selected at runtime is still decided by ``provider_config.provider_type`` +
``capabilities()`` (I6) — nothing here is a hardcoded routing default.

Model presets are curated UI hints only. Anthropic uses the real current ids from CLAUDE.md
(claude-opus-4-8 / claude-sonnet-4-6 / claude-haiku-4-5-20251001). base_url values are the
OpenAI-compatible endpoints that the ApiProvider keys off; ``null`` means "Anthropic-native"
(anthropic) or "no HTTP endpoint" (local/cli), or "must be set per deployment" (azure-openai).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["VENDORS", "VendorInfo", "vendor_by_id"]


class VendorInfo(BaseModel):
    """One vendor row in the Settings catalog (W1)."""

    id: str = Field(description="Stable vendor id (catalog key).")
    display_name: str = Field(description="Human-readable vendor name for the UI.")
    provider_type: str = Field(
        description="Backend family: api | local | cli (maps to I6 routing)."
    )
    default_base_url: str | None = Field(
        default=None,
        description=(
            "OpenAI-compatible endpoint the ApiProvider keys off. null = Anthropic-native "
            "(anthropic), no HTTP endpoint (local/cli), or must be set per deployment "
            "(azure-openai). The user can always override in the row."
        ),
    )
    needs_api_key: bool = Field(
        description="True iff this vendor requires an API key (api vendors); false for local/cli."
    )
    model_presets: list[str] = Field(
        default_factory=list,
        description="Known model ids for the dropdown; the user may type a custom id.",
    )
    notes: str = Field(default="", description="Short UI help text for the vendor row.")


# ── The static catalog. Order is the display order in the Settings UI. ────────────
VENDORS: list[VendorInfo] = [
    VendorInfo(
        id="anthropic",
        display_name="Anthropic (Claude)",
        provider_type="api",
        default_base_url=None,  # null ⇒ Anthropic-native Messages API path
        needs_api_key=True,
        # Real current ids from CLAUDE.md §12 (no hardcoded ids elsewhere).
        model_presets=["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        notes="Native Anthropic Messages API. Leave base URL empty for api.anthropic.com.",
    ),
    VendorInfo(
        id="claude-cli",
        display_name="Claude Code CLI",
        provider_type="cli",
        default_base_url=None,
        needs_api_key=False,
        model_presets=["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        notes=(
            "Agentic CLI backend (claude-agent-sdk). Auth via the CLI subscription OAuth token "
            "(Provider → CLI auth) or ANTHROPIC_API_KEY env — not a per-vendor UI key."
        ),
    ),
    VendorInfo(
        id="codex-cli",
        display_name="Codex CLI",
        provider_type="cli",
        default_base_url=None,
        needs_api_key=False,
        model_presets=[],
        notes="OpenAI Codex agentic CLI backend. Auth handled by the CLI itself, not a UI key.",
    ),
    VendorInfo(
        id="openai",
        display_name="OpenAI (GPT)",
        provider_type="api",
        default_base_url="https://api.openai.com/v1",
        needs_api_key=True,
        model_presets=["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3", "o4-mini"],
        notes="OpenAI-compatible chat/completions endpoint.",
    ),
    VendorInfo(
        id="gemini",
        display_name="Google Gemini",
        provider_type="api",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        needs_api_key=True,
        model_presets=["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
        notes="Google Gemini via its OpenAI-compatible endpoint.",
    ),
    VendorInfo(
        id="azure-openai",
        display_name="Azure OpenAI",
        provider_type="api",
        default_base_url=None,
        needs_api_key=True,
        model_presets=["gpt-4o", "gpt-4o-mini", "gpt-4.1"],
        notes=(
            "Set base URL per resource/deployment, e.g. "
            "https://<resource>.openai.azure.com/openai/deployments/<deployment>. "
            "Model id = your deployment name."
        ),
    ),
    VendorInfo(
        id="deepseek",
        display_name="DeepSeek",
        provider_type="api",
        default_base_url="https://api.deepseek.com/v1",
        needs_api_key=True,
        model_presets=["deepseek-chat", "deepseek-reasoner"],
        notes="OpenAI-compatible. deepseek-reasoner streams chain-of-thought (reasoning_content).",
    ),
    VendorInfo(
        id="atlas-cloud",
        display_name="Atlas Cloud",
        provider_type="api",
        default_base_url=None,
        needs_api_key=True,
        model_presets=[],
        notes="OpenAI-compatible inference gateway. Set base URL + model id from your account.",
    ),
    VendorInfo(
        id="groq",
        display_name="Groq",
        provider_type="api",
        default_base_url="https://api.groq.com/openai/v1",
        needs_api_key=True,
        model_presets=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "deepseek-r1-distill-llama-70b",
        ],
        notes="OpenAI-compatible, low-latency hosted open models.",
    ),
    VendorInfo(
        id="xai",
        display_name="xAI (Grok)",
        provider_type="api",
        default_base_url="https://api.x.ai/v1",
        needs_api_key=True,
        model_presets=["grok-4", "grok-3", "grok-3-mini"],
        notes="OpenAI-compatible xAI Grok endpoint.",
    ),
    VendorInfo(
        id="nvidia-nim",
        display_name="NVIDIA NIM",
        provider_type="api",
        default_base_url="https://integrate.api.nvidia.com/v1",
        needs_api_key=True,
        model_presets=[
            "meta/llama-3.3-70b-instruct",
            "deepseek-ai/deepseek-r1",
            "qwen/qwen2.5-coder-32b-instruct",
        ],
        notes="OpenAI-compatible NVIDIA-hosted NIM microservices.",
    ),
    VendorInfo(
        id="kimi-moonshot",
        display_name="Kimi (Moonshot, global)",
        provider_type="api",
        default_base_url="https://api.moonshot.ai/v1",
        needs_api_key=True,
        model_presets=[
            "kimi-k2-0711-preview",
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
        ],
        notes="Moonshot AI global (OpenAI-compatible).",
    ),
    VendorInfo(
        id="kimi-cn",
        display_name="Kimi (Moonshot, CN)",
        provider_type="api",
        default_base_url="https://api.moonshot.cn/v1",
        needs_api_key=True,
        model_presets=[
            "kimi-k2-0711-preview",
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
        ],
        notes="Moonshot AI China endpoint (OpenAI-compatible).",
    ),
    VendorInfo(
        id="kimi-coding",
        display_name="Kimi Coding",
        provider_type="api",
        default_base_url="https://api.moonshot.ai/v1",
        needs_api_key=True,
        model_presets=["kimi-k2-0711-preview"],
        notes="Kimi coding-tuned models via the Moonshot OpenAI-compatible endpoint.",
    ),
    VendorInfo(
        id="ollama",
        display_name="Ollama (Local)",
        provider_type="local",
        default_base_url=None,  # resolved from OLLAMA_URL env / provider_config base_url
        needs_api_key=False,
        model_presets=["llama3.1", "qwen2.5", "mistral", "gemma2"],
        notes="Local Ollama server (RTX 3060). Base URL from OLLAMA_URL env or the row; no key.",
    ),
]

_BY_ID: dict[str, VendorInfo] = {v.id: v for v in VENDORS}


def vendor_by_id(vendor_id: str) -> VendorInfo | None:
    """Return the catalog entry for *vendor_id*, or None if unknown."""
    return _BY_ID.get(vendor_id)
