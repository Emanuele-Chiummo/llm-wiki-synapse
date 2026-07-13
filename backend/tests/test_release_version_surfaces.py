"""Release version consistency across manifests and lockfiles."""

from __future__ import annotations

import importlib.util
import json
import tomllib
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
        "extension",
    }
    assert None not in versions.values()
    assert len(set(versions.values())) == 1


def test_public_metadata_uses_shared_positioning() -> None:
    root = Path(__file__).resolve().parents[2]
    descriptor = "The self-hosted LLM wiki that turns your sources into connected knowledge."

    readme = (root / "README.md").read_text()
    assert f"**{descriptor}**" in readme
    assert "*Connect everything.*" in readme

    frontend_package = json.loads((root / "frontend" / "package.json").read_text())
    assert frontend_package["description"] == f"Synapse — {descriptor}"

    backend_package = tomllib.loads((root / "backend" / "pyproject.toml").read_text())
    assert backend_package["project"]["description"] == f"Synapse — {descriptor}"

    extension = json.loads((root / "extension" / "manifest.json").read_text())
    assert extension["version"] == "1.6.0"
    assert extension["description"] == f"Clip web articles to Synapse. {descriptor}"
