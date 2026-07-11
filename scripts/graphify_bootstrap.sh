#!/usr/bin/env bash
# graphify_bootstrap.sh — SessionStart hook for Synapse.
#
# Purpose: make the project's graphify knowledge-graph "memory" available in EVERY
# Claude Code session (interactive or a daily-driver run) at ZERO token cost.
#
# Design:
#   - graphify's `update` re-extracts code with AST parsers only — NO LLM, no tokens.
#   - graph.json (~18MB) is gitignored, so a fresh clone has no memory; we rebuild it.
#   - A full rebuild takes ~1-3 min, so we run it in the BACKGROUND and exit 0 fast.
#     The daily driver additionally runs a blocking `graphify update .` as its first
#     step, guaranteeing fresh memory before it starts working.
#
# This hook MUST fail soft: it never blocks or breaks a session. Every path exits 0.
set +e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 0
export PATH="$HOME/.local/bin:$PATH"
LOG="$REPO_ROOT/.graphify-bootstrap.log"

{
  echo "=== graphify bootstrap $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

  # 1. Ensure the CLI is installed (idempotent; only pays cost on a cold container).
  if ! command -v graphify >/dev/null 2>&1; then
    if command -v uv >/dev/null 2>&1; then
      uv tool install graphifyy >/dev/null 2>&1
    elif command -v pipx >/dev/null 2>&1; then
      pipx install graphifyy >/dev/null 2>&1
    fi
  fi

  if ! command -v graphify >/dev/null 2>&1; then
    echo "graphify unavailable (no uv/pipx or install failed) — skipping, session unaffected."
    exit 0
  fi

  # 2. Register the skill for Claude if not already present (idempotent).
  if [ ! -f "$HOME/.claude/skills/graphify/SKILL.md" ]; then
    graphify install --platform claude >/dev/null 2>&1
  fi

  # 3. Refresh the code memory in the background (AST-only, zero tokens).
  #    A warm graphify cache makes this near-instant on subsequent runs.
  nohup graphify update . >>"$LOG" 2>&1 &
  echo "graphify update launched in background (pid $!). Memory will be ready shortly."
} >>"$LOG" 2>&1

exit 0
