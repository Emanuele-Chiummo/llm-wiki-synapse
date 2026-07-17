#!/usr/bin/env python3
"""
parity_report.py — 1:1 structural parity scorecard (ADR-0067).

Greps a Synapse vault and the LLM Wiki gold vault on disk and prints the acceptance
table from docs/reference/AUDIT-SYNAPSE-VS-LLMWIKI-1TO1-2026-07-10.md §5.C. Stdlib only;
never mutates a vault. Run after every alignment wave / KB reload.

Usage:
    python scripts/parity_report.py \
        --synapse "/Volumes/synapse/vault" \
        --gold    "/path/to/llm-wiki-reference"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_WIKI_SUBDIRS = ["entities", "concepts", "sources", "queries", "synthesis", "comparisons"]
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def _wiki_root(vault: Path) -> Path:
    """Return the dir that actually holds the type folders (some vaults nest under wiki/)."""
    if (vault / "wiki").is_dir():
        return vault / "wiki"
    return vault


def _count_dir(root: Path, name: str) -> int:
    d = root / name
    return sum(1 for _ in d.glob("*.md")) if d.is_dir() else 0


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _iter_pages(root: Path):
    for sub in _WIKI_SUBDIRS:
        d = root / sub
        if d.is_dir():
            for f in d.glob("*.md"):
                yield f


def _frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        parts = text.split("---\n", 2)
        if len(parts) == 3:
            return parts[1]
    return ""


def _slugify(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return s.strip("-")


def analyse(vault: Path) -> dict:
    root = _wiki_root(vault)
    counts = {sub: _count_dir(root, sub) for sub in _WIKI_SUBDIRS}

    # existing page slugs (for wikilink resolution) + titles
    slugs: set[str] = set()
    titles: set[str] = set()
    for f in _iter_pages(root):
        slugs.add(f.stem.lower())
        m = re.search(r"^title:\s*(.+)$", _frontmatter(_read(f)), re.MULTILINE)
        if m:
            titles.add(m.group(1).strip().strip('"').strip("'"))

    lint_stubs = 0
    related_present = 0
    total_pages = 0
    link_slug = link_title = link_other = 0
    for f in _iter_pages(root):
        text = _read(f)
        fm = _frontmatter(text)
        total_pages += 1
        if "placeholder for a missing wikilink" in text.lower() or (
            "- stub" in fm and "- lint" in fm
        ):
            if f.parent.name == "queries":
                lint_stubs += 1
        if re.search(r"^related:\s*(\[|\n\s*-|\S)", fm, re.MULTILINE):
            # related: with a non-empty value or a list
            if not re.search(r"^related:\s*\[\s*\]\s*$", fm, re.MULTILINE):
                related_present += 1
        for m in _WIKILINK.finditer(text):
            tgt = m.group(1).strip()
            if tgt.lower() in slugs or _slugify(tgt) == tgt:
                link_slug += 1
            elif tgt in titles:
                link_title += 1
            else:
                link_other += 1

    # index.md / overview.md structure
    index_md = _read(root / "index.md")
    overview_md = _read(root / "overview.md")
    uncategorised = index_md.count("## Uncategorised")
    querys_bad = index_md.count("## Querys")
    index_entries = re.findall(r"^- \[\[[^\]]+\]\]", index_md, re.MULTILINE)
    index_glossed = sum(1 for ln in index_md.splitlines() if re.match(r"^- \[\[[^\]]+\]\].*—", ln))
    ov_fm = _frontmatter(overview_md)
    ov_tags = 0
    mtag = re.search(r"^tags:\s*\[([^\]]*)\]", ov_fm, re.MULTILINE)
    if mtag:
        ov_tags = len([t for t in mtag.group(1).split(",") if t.strip()])
    else:
        # block style
        block = re.search(r"^tags:\s*\n((?:\s*-\s*.+\n)+)", ov_fm, re.MULTILINE)
        if block:
            ov_tags = len(re.findall(r"-\s*.+", block.group(1)))
    ov_open_q = 1 if re.search(
        r"(?i)^#+\s*(open questions|tensioni irrisolte|domande aperte|question)", overview_md, re.M
    ) else 0

    total_links = link_slug + link_title + link_other or 1
    return {
        **counts,
        "lint_stubs": lint_stubs,
        "related_pct": round(100 * related_present / (total_pages or 1)),
        "uncategorised": uncategorised,
        "querys_bad": querys_bad,
        "index_entries": len(index_entries),
        "index_glossed_pct": round(100 * index_glossed / (len(index_entries) or 1)),
        "overview_tags": ov_tags,
        "overview_open_q": ov_open_q,
        "link_slug_pct": round(100 * link_slug / total_links),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synapse", required=True)
    ap.add_argument("--gold", required=True)
    args = ap.parse_args()

    syn = analyse(Path(args.synapse))
    gold = analyse(Path(args.gold))

    rows = [
        ("entities", "entities", "≥190"),
        ("concepts", "concepts", "~460-490"),
        ("sources", "sources", "—"),
        ("queries", "queries", "~90-120"),
        ("synthesis", "synthesis", "≥4"),
        ("comparisons", "comparisons", "≥5"),
        ("query lint-stubs", "lint_stubs", "0"),
        ("pages w/ related (%)", "related_pct", "~100"),
        ("index ## Uncategorised", "uncategorised", "0"),
        ("index ## Querys (bad)", "querys_bad", "0"),
        ("index glossed (%)", "index_glossed_pct", "100"),
        ("overview tags", "overview_tags", "≥100"),
        ("overview Open-Q block", "overview_open_q", "1"),
        ("wikilinks by slug (%)", "link_slug_pct", "≥80"),
    ]
    print(f"\n{'Metric':28} {'Synapse':>10} {'Gold':>10} {'Target':>12}")
    print("-" * 62)
    for label, key, target in rows:
        print(f"{label:28} {str(syn.get(key,'-')):>10} {str(gold.get(key,'-')):>10} {target:>12}")
    print()


if __name__ == "__main__":
    main()
