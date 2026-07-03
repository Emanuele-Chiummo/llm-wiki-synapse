"""
Tests for the CORS configuration defaults (ADR-0047 §C4).

Assertions:
  - Default cors_allow_origins includes both Vite dev origins.
  - Default cors_allow_origins includes tauri://localhost  (macOS/Linux WebKit).
  - Default cors_allow_origins includes http://tauri.localhost (Windows WebView2).
  - The wildcard "*" is NOT in the default list (forbidden with allow_credentials=True).
  - cors_origins_list property correctly splits and trims the comma-separated value.
"""

from __future__ import annotations

from app.config import Settings

# Read field defaults directly from the model class (no instantiation needed).
from pydantic.fields import FieldInfo

VITE_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

TAURI_ORIGINS = [
    "tauri://localhost",  # macOS / Linux — Tauri v2 WebKit webview
    "http://tauri.localhost",  # Windows — Tauri v2 WebView2
]

REQUIRED_ORIGINS = VITE_ORIGINS + TAURI_ORIGINS


def _get_cors_default() -> str:
    """Return the raw default string for cors_allow_origins without instantiating Settings."""
    field: FieldInfo = Settings.model_fields["cors_allow_origins"]
    default = field.default
    assert isinstance(default, str), "cors_allow_origins default must be a str"
    return default


class TestCorsOriginDefault:
    """Unit tests against the field default — no env vars required."""

    def test_default_contains_vite_dev_origins(self) -> None:
        """Both Vite dev-server origins must be in the default string."""
        raw = _get_cors_default()
        for origin in VITE_ORIGINS:
            assert origin in raw, (
                f"Expected Vite origin {origin!r} in cors_allow_origins default. " f"Got: {raw!r}"
            )

    def test_default_contains_tauri_macos_linux_origin(self) -> None:
        """tauri://localhost must be in the default (macOS/Linux WebKit webview, ADR-0047 §C4)."""
        raw = _get_cors_default()
        assert (
            "tauri://localhost" in raw
        ), f"Expected 'tauri://localhost' in cors_allow_origins default. Got: {raw!r}"

    def test_default_contains_tauri_windows_origin(self) -> None:
        """http://tauri.localhost must be in the default (Windows WebView2, ADR-0047 §C4)."""
        raw = _get_cors_default()
        assert (
            "http://tauri.localhost" in raw
        ), f"Expected 'http://tauri.localhost' in cors_allow_origins default. Got: {raw!r}"

    def test_default_does_not_contain_wildcard(self) -> None:
        """
        The wildcard '*' must NOT appear in the default list.

        allow_credentials=True forbids the CORS wildcard under the spec; a '*' default
        would silently break all credentialed preflights (ADR-0047 §2.4 / risk 3).
        """
        raw = _get_cors_default()
        parsed = [o.strip() for o in raw.split(",") if o.strip()]
        assert "*" not in parsed, (
            "cors_allow_origins default must never be '*' — "
            "forbidden with allow_credentials=True (ADR-0047 §2.4)."
        )

    def test_default_is_non_empty(self) -> None:
        """The default must produce a non-empty origins list."""
        raw = _get_cors_default()
        parsed = [o.strip() for o in raw.split(",") if o.strip()]
        assert (
            len(parsed) >= 4
        ), f"Expected at least 4 origins in the default list, got {len(parsed)}: {parsed}"


class TestCorsOriginsListProperty:
    """
    Tests for the cors_origins_list property using the module-level settings singleton.

    Following the established pattern in this test suite (e.g. test_clip_config.py,
    test_web_search_config.py), we mutate ``cfg.settings.cors_allow_origins`` directly
    and restore it in a finally block. This avoids constructing a new Settings instance
    (which would re-read .env / os.environ and may conflict with the test environment).
    """

    def test_cors_origins_list_contains_all_required_origins_from_default(self) -> None:
        """cors_origins_list must contain all 4 required origins when the setting is at default."""
        from app import config as cfg

        original = cfg.settings.cors_allow_origins
        try:
            # Explicitly set to the documented default value (ADR-0047 §C4).
            cfg.settings.cors_allow_origins = (
                "http://localhost:5173,"
                "http://127.0.0.1:5173,"
                "tauri://localhost,"
                "http://tauri.localhost"
            )
            origins = cfg.settings.cors_origins_list
            for origin in REQUIRED_ORIGINS:
                assert (
                    origin in origins
                ), f"cors_origins_list missing {origin!r}. Full list: {origins}"
        finally:
            cfg.settings.cors_allow_origins = original

    def test_cors_origins_list_no_wildcards_by_default(self) -> None:
        """cors_origins_list must not contain '*' in the default configuration."""
        from app import config as cfg

        original = cfg.settings.cors_allow_origins
        try:
            cfg.settings.cors_allow_origins = (
                "http://localhost:5173,"
                "http://127.0.0.1:5173,"
                "tauri://localhost,"
                "http://tauri.localhost"
            )
            assert (
                "*" not in cfg.settings.cors_origins_list
            ), "cors_origins_list must not contain '*' with allow_credentials=True"
        finally:
            cfg.settings.cors_allow_origins = original

    def test_cors_origins_list_strips_whitespace(self) -> None:
        """cors_origins_list trims surrounding whitespace from each entry."""
        from app import config as cfg

        original = cfg.settings.cors_allow_origins
        try:
            cfg.settings.cors_allow_origins = " http://localhost:5173 , http://127.0.0.1:5173 "
            for origin in cfg.settings.cors_origins_list:
                assert (
                    origin == origin.strip()
                ), f"Origin {origin!r} has leading/trailing whitespace"
        finally:
            cfg.settings.cors_allow_origins = original

    def test_cors_origins_list_filters_empty_entries(self) -> None:
        """cors_origins_list drops empty entries from a value with trailing/double commas."""
        from app import config as cfg

        original = cfg.settings.cors_allow_origins
        try:
            cfg.settings.cors_allow_origins = "http://localhost:5173,,http://127.0.0.1:5173,"
            result = cfg.settings.cors_origins_list
            assert "" not in result
            assert len(result) == 2
        finally:
            cfg.settings.cors_allow_origins = original

    def test_cors_origins_list_override_replaces_default(self) -> None:
        """Setting cors_allow_origins to a single entry replaces the default entirely."""
        from app import config as cfg

        original = cfg.settings.cors_allow_origins
        try:
            cfg.settings.cors_allow_origins = "http://example.com"
            result = cfg.settings.cors_origins_list
            assert result == ["http://example.com"]
            assert "tauri://localhost" not in result
        finally:
            cfg.settings.cors_allow_origins = original
