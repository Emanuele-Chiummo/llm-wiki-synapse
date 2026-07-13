"""Release version consistency across manifests and lockfiles."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def test_all_release_version_surfaces_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(root)
    script = root / "scripts" / "bump_version.py"
    spec = importlib.util.spec_from_file_location("bump_version", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    versions = module.get_all_versions()
    assert set(versions) == {
        "backend",
        "frontend",
        "cargo",
        "tauri",
        "frontend_lock",
        "cargo_lock",
    }
    assert None not in versions.values()
    assert len(set(versions.values())) == 1
