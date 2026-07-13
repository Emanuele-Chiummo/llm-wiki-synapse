#!/usr/bin/env python3
"""
Version bump helper for Synapse project.

Validates semver (X.Y.Z) and updates all 7 version surfaces:
  1. backend/pyproject.toml — version = "X.Y.Z"
  2. frontend/package.json — "version": "X.Y.Z"
  3. src-tauri/Cargo.toml — version = "X.Y.Z"
  4. src-tauri/tauri.conf.json — "version": "X.Y.Z"
  5. frontend/package-lock.json — root package version
  6. src-tauri/Cargo.lock — root `synapse` package version
  7. extension/manifest.json — browser extension version

Usage:
    python scripts/bump_version.py check [<version>]     # Check all 7 surfaces agree
                                                          # (default: current version)
    python scripts/bump_version.py bump <version>        # Update all 7 surfaces to <version>

Exit codes:
    0 — success
    1 — invalid semver format
    2 — version mismatch (files don't agree)
    3 — file I/O error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def validate_semver(version: str) -> bool:
    """Validate version is X.Y.Z format."""
    pattern = r"^\d+\.\d+\.\d+$"
    return bool(re.match(pattern, version))


def read_backend_version() -> str | None:
    """Read version from backend/pyproject.toml."""
    try:
        path = Path("backend/pyproject.toml")
        content = path.read_text()
        match = re.search(r'^version = "([^"]+)"', content, re.MULTILINE)
        return match.group(1) if match else None
    except Exception as e:
        print(f"Error reading backend/pyproject.toml: {e}", file=sys.stderr)
        return None


def read_frontend_version() -> str | None:
    """Read version from frontend/package.json."""
    try:
        path = Path("frontend/package.json")
        data = json.loads(path.read_text())
        return data.get("version")
    except Exception as e:
        print(f"Error reading frontend/package.json: {e}", file=sys.stderr)
        return None


def read_cargo_version() -> str | None:
    """Read version from src-tauri/Cargo.toml."""
    try:
        path = Path("src-tauri/Cargo.toml")
        content = path.read_text()
        match = re.search(r'^version = "([^"]+)"', content, re.MULTILINE)
        return match.group(1) if match else None
    except Exception as e:
        print(f"Error reading src-tauri/Cargo.toml: {e}", file=sys.stderr)
        return None


def read_tauri_conf_version() -> str | None:
    """Read version from src-tauri/tauri.conf.json."""
    try:
        path = Path("src-tauri/tauri.conf.json")
        data = json.loads(path.read_text())
        return data.get("version")
    except Exception as e:
        print(f"Error reading src-tauri/tauri.conf.json: {e}", file=sys.stderr)
        return None


def read_frontend_lock_version() -> str | None:
    """Read and validate the root version from frontend/package-lock.json."""
    try:
        data = json.loads(Path("frontend/package-lock.json").read_text())
        top_level = data.get("version")
        root_package = data.get("packages", {}).get("", {}).get("version")
        return top_level if top_level == root_package else None
    except Exception as e:
        print(f"Error reading frontend/package-lock.json: {e}", file=sys.stderr)
        return None


def read_cargo_lock_version() -> str | None:
    """Read the root Synapse package version from src-tauri/Cargo.lock."""
    try:
        content = Path("src-tauri/Cargo.lock").read_text()
        match = re.search(
            r'\[\[package\]\]\s+name = "synapse"\s+version = "([^"]+)"',
            content,
        )
        return match.group(1) if match else None
    except Exception as e:
        print(f"Error reading src-tauri/Cargo.lock: {e}", file=sys.stderr)
        return None


def read_extension_version() -> str | None:
    """Read the browser extension version from its MV3 manifest."""
    try:
        data = json.loads(Path("extension/manifest.json").read_text())
        return data.get("version")
    except Exception as e:
        print(f"Error reading extension/manifest.json: {e}", file=sys.stderr)
        return None


def get_all_versions() -> dict[str, str | None]:
    """Read every public release version surface."""
    return {
        "backend": read_backend_version(),
        "frontend": read_frontend_version(),
        "cargo": read_cargo_version(),
        "tauri": read_tauri_conf_version(),
        "frontend_lock": read_frontend_lock_version(),
        "cargo_lock": read_cargo_lock_version(),
        "extension": read_extension_version(),
    }


def check_versions(expected: str | None = None) -> tuple[bool, str]:
    """
    Check that all release surfaces agree on the version.

    Args:
        expected: if provided, also check that all files match this version

    Returns:
        (success, message) tuple
    """
    versions = get_all_versions()

    # Check for read errors
    if any(v is None for v in versions.values()):
        failed = [k for k, v in versions.items() if v is None]
        return False, f"Failed to read version from: {', '.join(failed)}"

    # Check all files agree
    unique_versions = set(versions.values())
    if len(unique_versions) > 1:
        msg = "Version mismatch across files:\n"
        for name, version in versions.items():
            msg += f"  {name}: {version}\n"
        return False, msg.rstrip()

    current = versions["backend"]

    # If expected version provided, check it matches
    if expected:
        if current != expected:
            return False, f"Expected {expected}, but files have {current}"

    return True, f"All files agree: {current}"


def bump_version(new_version: str) -> tuple[bool, str]:
    """
    Update all release surfaces to new_version.

    Args:
        new_version: X.Y.Z version string (must be validated by caller)

    Returns:
        (success, message) tuple
    """
    try:
        # backend/pyproject.toml
        backend_path = Path("backend/pyproject.toml")
        backend_content = backend_path.read_text()
        backend_content = re.sub(
            r'(^version = )"[^"]+"', f'\\1"{new_version}"', backend_content, flags=re.MULTILINE
        )
        backend_path.write_text(backend_content)

        # frontend/package.json — preserve exact formatting.
        # ensure_ascii=False avoids escaping the em dash in public copy.
        frontend_path = Path("frontend/package.json")
        frontend_data = json.loads(frontend_path.read_text())
        frontend_data["version"] = new_version
        # Keep the existing 2-space format and preserve Unicode characters.
        frontend_path.write_text(json.dumps(frontend_data, indent=2, ensure_ascii=False) + "\n")

        # src-tauri/Cargo.toml
        cargo_path = Path("src-tauri/Cargo.toml")
        cargo_content = cargo_path.read_text()
        cargo_content = re.sub(
            r'(^version = )"[^"]+"', f'\\1"{new_version}"', cargo_content, flags=re.MULTILINE
        )
        cargo_path.write_text(cargo_content)

        # src-tauri/tauri.conf.json — preserve exact formatting
        tauri_path = Path("src-tauri/tauri.conf.json")
        tauri_data = json.loads(tauri_path.read_text())
        tauri_data["version"] = new_version
        # Keep the existing 2-space format and preserve Unicode characters.
        tauri_path.write_text(json.dumps(tauri_data, indent=2, ensure_ascii=False) + "\n")

        # frontend/package-lock.json — keep both root version fields aligned with package.json
        frontend_lock_path = Path("frontend/package-lock.json")
        frontend_lock_data = json.loads(frontend_lock_path.read_text())
        frontend_lock_data["version"] = new_version
        frontend_lock_data.setdefault("packages", {}).setdefault("", {})["version"] = new_version
        frontend_lock_path.write_text(
            json.dumps(frontend_lock_data, indent=2, ensure_ascii=False) + "\n"
        )

        # src-tauri/Cargo.lock — update only the workspace package stanza, never dependencies
        cargo_lock_path = Path("src-tauri/Cargo.lock")
        cargo_lock_content = cargo_lock_path.read_text()
        cargo_lock_content, replacements = re.subn(
            r'(\[\[package\]\]\s+name = "synapse"\s+version = ")[^"]+("\s+)',
            rf"\g<1>{new_version}\g<2>",
            cargo_lock_content,
            count=1,
        )
        if replacements != 1:
            raise ValueError("root synapse package not found in src-tauri/Cargo.lock")
        cargo_lock_path.write_text(cargo_lock_content)

        # Browser extension MV3 manifest
        extension_path = Path("extension/manifest.json")
        extension_data = json.loads(extension_path.read_text())
        extension_data["version"] = new_version
        extension_path.write_text(json.dumps(extension_data, indent=2, ensure_ascii=False) + "\n")

        return True, f"Bumped version to {new_version}"

    except Exception as e:
        return False, f"Error writing files: {e}"


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Version bump helper for Synapse.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # check subcommand
    check_parser = subparsers.add_parser("check", help="Check that all 7 surfaces agree on version")
    check_parser.add_argument(
        "version",
        nargs="?",
        help="Expected version (optional; if provided, also validate against this)",
    )

    # bump subcommand
    bump_parser = subparsers.add_parser("bump", help="Bump version in all 7 surfaces")
    bump_parser.add_argument("version", help="New version (X.Y.Z format)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "check":
        success, msg = check_versions(args.version)
        print(msg)
        return 0 if success else 2

    if args.command == "bump":
        version = args.version
        if not validate_semver(version):
            print(f"Invalid semver format: {version} (expected X.Y.Z)", file=sys.stderr)
            return 1

        success, msg = bump_version(version)
        print(msg)
        if success:
            # Verify the bump
            success, verify_msg = check_versions(version)
            print(verify_msg)
            return 0 if success else 2
        return 3

    return 1


if __name__ == "__main__":
    sys.exit(main())
