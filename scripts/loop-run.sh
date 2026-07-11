#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Synapse autonomous-loop runner — invoked by launchd (see scripts/launchd/).
#
#   Usage: loop-run.sh <daily|release>
#     daily    run ONE bounded daily block per docs/process/DAILY-DRIVER.md §2
#     release  run the weekly release consolidation per §4.5 (ONE PR, never merges)
#
# This wrapper is deliberately THIN. All real logic (branch selection, block
# choice, git, the 360° test gate, dashboard publish) lives in the driver, which
# the spawned headless `claude -p` session reads top-to-bottom. The wrapper only
# owns the environment, logging, a single-instance lock, keep-awake, and a
# wall-clock runaway guard.
#
# Env knobs:
#   DRY_RUN=1                        skip the `claude -p` call — validate plumbing only
#   LOOP_PERMISSION_MODE=<mode>      permission mode for the headless run.
#                                    Default 'acceptEdits' (SAFE: auto-accepts file edits
#                                    but still pauses for shell/other tools — so it is NOT
#                                    truly unattended). For genuine no-input autonomy the
#                                    OWNER must explicitly opt in with 'bypassPermissions'.
# ---------------------------------------------------------------------------
set -uo pipefail

MODE="${1:-daily}"
REPO="/Users/emanuelechiummo/Documents/00_Personal/00_Claude/LLM Wiki Project"

# launchd hands us a minimal environment — set an explicit, complete PATH.
export HOME="${HOME:-/Users/emanuelechiummo}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$HOME/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

LOG_DIR="$HOME/Library/Logs/synapse-loop"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG="$LOG_DIR/${MODE}-${STAMP}.log"

log() { echo "[$(date -u +%FT%TZ)] $*" >>"$LOG"; }

# --- Single-instance lock (atomic mkdir). Skip if a prior run is still alive. ---
LOCK="$LOG_DIR/.${MODE}.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  log "SKIP — a ${MODE} run is already in progress ($LOCK)."
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

cd "$REPO" || { log "FATAL — repo not found: $REPO"; exit 1; }

# --- Mode-specific prompt + model (driver is the single source of truth). ---
case "$MODE" in
  daily)
    MODEL="claude-sonnet-5"
    CAP=10800   # 3h hard wall-clock cap
    PROMPT='You are the Synapse autonomous daily-driver. No human is present. Read docs/process/DAILY-DRIVER.md and execute EXACTLY ONE bounded block, following §2 Phases 0 through 5 in order. Obey the golden rules in §1: one block per day; if the queued item is too big, SPLIT it and do only the first sub-block; target no more than ~150k output tokens and log total_cost_usd; use graphify query/explain/affected instead of re-reading files; pick the cheapest capable model per the §4 routing table (NOT always Opus); never touch invariants I1-I9 silently. Interleave per §2 Phase 1: even day-of-month picks the bug/hardening lane, odd day-of-month picks the llm_wiki parity lane (fall back to the other lane if empty), taking the TOP unchecked queue item in §3. Persist per §4: commit and push to the weekly integration branch claude/weekly-<SAT> where <SAT> is the YYYY-MM-DD of the Saturday that opened the current Saturday-to-Friday cycle; create it off origin/main if missing; do NOT open a PR (the Friday release run does that). Keep the §5 status files and the dashboard current. Finish with the 5-line summary and STOP.'
    ;;
  release)
    MODEL="claude-sonnet-5"
    CAP=14400   # 4h hard wall-clock cap
    PROMPT='You are the Synapse weekly release runner. No human is present. Read docs/process/DAILY-DRIVER.md §4.5 and run the weekly release for the current Saturday-to-Friday cycle: git fetch origin; identify and rebase the cycle branch claude/weekly-<SAT> onto origin/main; run the full 360 test gate (make lint, make typecheck, make test; the frontend suite if the frontend was touched; make er and make openapi must show ZERO git diff if schema or routes moved); pick the semver bump (patch or minor) for what actually shipped and run make bump VERSION=x.y.z; finalize CHANGELOG.md by moving [Unreleased] to [x.y.z] with today as the date; then open EXACTLY ONE PR (base main, head claude/weekly-<SAT>) mirroring .github/PULL_REQUEST_TEMPLATE.md and summarizing every block shipped this week. NEVER merge — the owner reviews and merges. If nothing shippable accrued this week, skip the release (log "no release this week") and do NOT open an empty PR. Update the dashboard/status files and STOP.'
    ;;
  *)
    echo "usage: loop-run.sh <daily|release>" >&2
    exit 2
    ;;
esac

log "START ${MODE} — model=${MODEL} cap=${CAP}s cwd=$REPO"

if [ "${DRY_RUN:-0}" = "1" ]; then
  log "DRY_RUN=1 — plumbing OK, skipping the claude -p call."
  log "END ${MODE} — dry-run exit=0"
  exit 0
fi

# --- Headless run. Permission mode is an explicit OWNER decision (see header). ---
# Default 'acceptEdits' is safe but not truly unattended; 'bypassPermissions' (opt-in)
# is what makes the loop autonomous. Safety net around bypass: scoped to the repo
# (cwd + --add-dir), the loop NEVER merges (human gate on the weekly PR), every run is
# fully logged here, and cost is bounded by the driver's token budget (I7).
PERM_MODE="${LOOP_PERMISSION_MODE:-acceptEdits}"
log "permission-mode=${PERM_MODE}$([ "$PERM_MODE" != bypassPermissions ] && echo '  (NOT fully unattended — set LOOP_PERMISSION_MODE=bypassPermissions to authorize)')"
claude -p "$PROMPT" \
    --model "$MODEL" \
    --permission-mode "$PERM_MODE" \
    --add-dir "$REPO" \
    >>"$LOG" 2>&1 &
CLAUDE_PID=$!

# Keep the Mac awake until the run finishes (laptop may be idle at 07:03).
caffeinate -i -w "$CLAUDE_PID" &
# Runaway guard: hard-kill if the run exceeds the wall-clock cap.
( sleep "$CAP"; kill -TERM "$CLAUDE_PID" 2>/dev/null ) &
WATCH_PID=$!

wait "$CLAUDE_PID"; RC=$?
kill "$WATCH_PID" 2>/dev/null

log "END ${MODE} — exit=$RC"

# Retain only the last 30 logs per mode.
ls -1t "$LOG_DIR/${MODE}-"*.log 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true
exit "$RC"
