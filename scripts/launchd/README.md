# Synapse autonomous-loop scheduling (macOS / launchd)

Local, durable scheduling for the [Daily Driver](../../docs/process/DAILY-DRIVER.md).
Two `launchd` LaunchAgents drive the loop; both invoke the thin wrapper
[`scripts/loop-run.sh`](../loop-run.sh), which starts a headless `claude -p`
session that reads the driver top-to-bottom and does the actual work.

| Job | Label | Schedule | Action |
|-----|-------|----------|--------|
| Daily driver | `com.synapse.daily-driver` | **07:03 every day** (`3 7 * * *`) | Run ONE bounded block (driver §2), commit + push to `claude/weekly-<SAT>`, **no PR** |
| Weekly release | `com.synapse.weekly-release` | **Fri 18:03** (`3 18 * * 5`) | Consolidate the Sat→Fri cycle into **ONE PR** to `main` (driver §4.5), **never merges** |

## Why launchd (not cron / not a session-cron)

- **Survives session end.** Unlike `CronCreate` session-crons (7-day cap, die with
  the session, need this chat open), a LaunchAgent is registered with the OS.
- **Catches up after sleep.** launchd runs a missed `StartCalendarInterval` job once
  on the next wake — a laptop asleep at 07:03 still gets its block. Stock `cron` does not.
- **Local, so it can reach the homelab** (Postgres/Qdrant/Ollama/SearXNG) for real 360° tests.

> LaunchAgents run only while the user is **logged into the GUI session**. Keep the Mac
> on and logged in for unattended runs. When the durable cloud Routine (`create_trigger`)
> permission is granted, that becomes the preferred vehicle (driver §6) and these can be removed.

## Install / manage

```bash
bash scripts/launchd/install.sh      # install + load both jobs (idempotent)
bash scripts/launchd/uninstall.sh    # remove both jobs

launchctl list | grep com.synapse                                 # are they loaded?
launchctl print gui/$(id -u)/com.synapse.daily-driver             # full job state + next run
DRY_RUN=1 bash scripts/loop-run.sh daily                          # test plumbing (no tokens, no git)
launchctl kickstart -k gui/$(id -u)/com.synapse.daily-driver      # fire a REAL run now
tail -f ~/Library/Logs/synapse-loop/daily-*.log                   # watch a run
```

## ⚠️ Owner authorization required for full autonomy

Both jobs default to **`LOOP_PERMISSION_MODE=acceptEdits`** (set in each plist). That mode
is safe but **pauses for shell tools**, so the loop is *not truly unattended* until you
opt in. To authorize genuine no-input autonomy, edit the installed plist
(`~/Library/LaunchAgents/com.synapse.*.plist`), set `LOOP_PERMISSION_MODE` to
**`bypassPermissions`**, and reload (`bash scripts/launchd/install.sh`). This is a
deliberate, explicit sandbox opt-in — nothing enables it for you.

## Runner safety (`loop-run.sh`)

- **`bypassPermissions` (opt-in only)** — mitigated by: scoped to the repo (`cwd` +
  `--add-dir`), the loop **never merges** (owner reviews the weekly PR), every run is
  fully logged, and cost is bounded by the driver's token budget (I7).
- **Single-instance lock** — a hung run won't be double-started the next day.
- **`caffeinate -i`** — prevents idle sleep for the duration of a run.
- **Wall-clock guard** — hard-kills a runaway run (daily 3h / release 4h).
- **Model** — `claude-sonnet-5` main loop; the orchestrator delegates to Haiku/Opus
  subagents per the driver §4 routing table (not "always Opus").

## Known limitation — dashboard Artifact

The daily run regenerates and commits `docs/dashboard/index.html`, but **re-publishing the
hosted Artifact** at the stable URL (driver §5) uses a claude.ai-only tool that a local
headless `claude -p` run may not have. The HTML is versioned regardless, so no data is lost;
the hosted page may just lag until refreshed from an interactive session.
