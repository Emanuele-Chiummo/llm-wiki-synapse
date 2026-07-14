#!/usr/bin/env python3
"""
Parity E2E comparator (WS-G, ADR-0083) — score a Synapse vault against an llm_wiki gold vault.

Both apps ingest the SAME 3 corpus documents (backend/tests/fixtures/parity_corpus/) with the
Claude CLI provider, then this script walks the two resulting `wiki/` trees and checks that the
Synapse output falls inside tolerance bands around the gold (LLM nondeterminism at temp 0.1 means
we compare distributions, not bytes — see docs/process/PARITY-E2E-RUNBOOK.md).

Reuses `analyse()` from scripts/parity_report.py for the per-vault structural metrics and adds a
wikilink-density measure + a regression sentinel. Prints a markdown scorecard; exits non-zero if
any band is violated so it can gate a release.

Usage:
    python scripts/parity_e2e/compare.py --gold <gold_vault> --candidate <synapse_vault> \
        [--baseline-links N]   # N = the recorded 1.5.6 wikilink total for the sentinel
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Reuse the per-vault analyser from the existing structural comparator.
_SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS))
from parity_report import (  # noqa: E402  (path injected above)
    _iter_pages,
    _read,
    _wiki_root,
    analyse,
)

_WIKILINK = re.compile(r"\[\[([^\]|\n]+)(?:\|[^\]\n]*)?\]\]")
_TYPE_SUBDIRS = (
    "entities",
    "concepts",
    "sources",
    "queries",
    "comparisons",
    "synthesis",
)


def wikilink_stats(vault: Path) -> dict[str, float]:
    """Total [[wikilinks]] and mean density over non-source content pages."""
    root = _wiki_root(vault)
    total_links = 0
    non_source_pages = 0
    non_source_links = 0
    for f in _iter_pages(root):
        text = _read(f)
        n = len(_WIKILINK.findall(text))
        total_links += n
        if f.parent.name != "sources":
            non_source_pages += 1
            non_source_links += n
    density = (non_source_links / non_source_pages) if non_source_pages else 0.0
    return {
        "total_links": float(total_links),
        "density": density,
        "pages": float(non_source_pages),
    }


def _band_pages_per_type(gold: int, cand: int) -> tuple[bool, str]:
    """Per-type count is OK within ±1 OR ±40% of gold, whichever is larger (plan tolerance)."""
    allowed = max(1, round(gold * 0.4))
    ok = abs(cand - gold) <= allowed
    return ok, f"±{allowed} (gold {gold} → cand {cand})"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Synapse vs llm_wiki parity scorecard (WS-G)."
    )
    ap.add_argument("--gold", required=True, type=Path, help="llm_wiki gold vault root")
    ap.add_argument("--candidate", required=True, type=Path, help="Synapse vault root")
    ap.add_argument(
        "--baseline-links",
        type=int,
        default=0,
        help="Recorded 1.5.6 wikilink total; the candidate must be >= this (regression sentinel).",
    )
    args = ap.parse_args()

    gold = analyse(args.gold)
    cand = analyse(args.candidate)
    gold_wl = wikilink_stats(args.gold)
    cand_wl = wikilink_stats(args.candidate)

    rows: list[tuple[str, str, bool]] = []

    # 1. Source pages — one per corpus doc; the candidate must match the gold's source count
    #    (the corpus size is whatever was ingested, not a fixed number).
    rows.append(
        (
            "source pages: candidate == gold",
            f"gold {gold['sources']} → cand {cand['sources']}",
            gold["sources"] == cand["sources"] and cand["sources"] > 0,
        )
    )

    # 2. Pages per type — per-type band; total within ±30%.
    for sub in _TYPE_SUBDIRS:
        if sub == "sources":
            continue
        ok, detail = _band_pages_per_type(int(gold.get(sub, 0)), int(cand.get(sub, 0)))
        rows.append((f"pages[{sub}]", detail, ok))
    gt = sum(int(gold.get(s, 0)) for s in _TYPE_SUBDIRS)
    ct = sum(int(cand.get(s, 0)) for s in _TYPE_SUBDIRS)
    total_ok = abs(ct - gt) <= max(1, round(gt * 0.3))
    rows.append(("total pages ±30%", f"gold {gt} → cand {ct}", total_ok))

    # 3. Wikilink density — candidate mean >= 0.7x gold AND <= 2x gold (no under- or over-shoot).
    g_d, c_d = gold_wl["density"], cand_wl["density"]
    dens_ok = (c_d >= 0.7 * g_d) and (c_d <= 2.0 * max(g_d, 1e-9))
    rows.append(
        (
            "wikilink density 0.7x–2x gold",
            f"gold {g_d:.2f} → cand {c_d:.2f}/page",
            dens_ok,
        )
    )

    # 4. Regression sentinel — candidate total links >= the recorded 1.5.6 baseline (the bug).
    if args.baseline_links:
        sent_ok = cand_wl["total_links"] >= args.baseline_links
        rows.append(
            (
                "total links >= 1.5.6 baseline",
                f"{cand_wl['total_links']:.0f} >= {args.baseline_links}",
                sent_ok,
            )
        )

    # 5. Wikilink resolution — candidate resolves to real slugs at a healthy rate (>= 0.6x gold).
    res_ok = cand["link_slug_pct"] >= 0.6 * max(gold["link_slug_pct"], 1)
    rows.append(
        (
            "link resolution >= 0.6x gold",
            f"gold {gold['link_slug_pct']}% → cand {cand['link_slug_pct']}%",
            res_ok,
        )
    )

    # 6. index.md shape — a '## Recently Updated' section with glossed entries.
    cand_index = _read(_wiki_root(args.candidate) / "index.md")
    ru_ok = "## Recently Updated" in cand_index
    rows.append(
        ("index '## Recently Updated' present", "yes" if ru_ok else "MISSING", ru_ok)
    )

    # 7. log.md shape — 3 ingest entries in the '## [YYYY-MM-DD] ingest | Title' format.
    cand_log = _read(_wiki_root(args.candidate) / "log.md")
    log_entries = re.findall(r"(?m)^## \[\d{4}-\d{2}-\d{2}\]\s+ingest\s*\|", cand_log)
    log_ok = len(log_entries) >= 3
    rows.append(("log has >=3 'ingest |' entries", f"{len(log_entries)}", log_ok))

    print("\n# Parity E2E scorecard — Synapse vs llm_wiki\n")
    print("| check | detail | verdict |")
    print("|---|---|---|")
    for name, detail, ok in rows:
        print(f"| {name} | {detail} | {'✅' if ok else '❌'} |")
    failed = [r for r in rows if not r[2]]
    print(f"\n**{len(rows) - len(failed)}/{len(rows)} bands passed.**")
    if failed:
        print("\nOut-of-band:")
        for name, detail, _ in failed:
            print(f"- {name}: {detail}")
        return 1
    print("\nAll bands passed — the vaults are comparable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
