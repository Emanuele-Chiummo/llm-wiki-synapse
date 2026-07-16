#!/usr/bin/env python3
"""
check_adr_index.py — ADR corpus consistency gate (1.9.0 W1, QA-DEBT-2).

The ADR index (docs/adr/index.md) is hand-curated (thematic sections are human
judgment) but MUST stay complete and resolvable. This script fails CI when:

  1. an ADR file does not follow the naming convention ``NNNN-kebab-slug.md``
     (the legacy ``ADR-NNNN-*.md`` convention was retired in 1.9.0);
  2. an ADR file exists but is not linked from index.md;
  3. index.md links to an ADR file that does not exist;
  4. an ADR file is missing the ``# ADR-NNNN — Title`` H1 or a Status line.

Numbering gaps are reported as warnings only (0082 is a known historical gap).

Usage:  python scripts/check_adr_index.py   (exit 0 = clean, 1 = violations)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADR_DIR = REPO / "docs" / "adr"
INDEX = ADR_DIR / "index.md"

NAME_RE = re.compile(r"^(\d{4})-[a-z0-9][a-z0-9-]*\.md$")
H1_RE = re.compile(r"^#\s+ADR-(\d{4})\s+[—-]", re.MULTILINE)
# Historical ADRs use several legitimate status formats: "- **Status:** X",
# "> Status: X" (blockquote), "| Status | X |" (table), and Italian "**Stato:**".
STATUS_RE = re.compile(
    r"^\s*(?:[>|\-*]\s*)*\**(?:Status|Stato)\b", re.IGNORECASE | re.MULTILINE
)
LINK_RE = re.compile(r"\]\((\d{4}-[a-z0-9-]+\.md)\)")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    adr_files = sorted(
        p.name for p in ADR_DIR.glob("*.md") if p.name not in {"index.md", "README.md"}
    )

    # 1. naming convention
    numbers: list[int] = []
    for name in adr_files:
        m = NAME_RE.match(name)
        if not m:
            errors.append(f"naming: {name} does not match NNNN-kebab-slug.md")
            continue
        numbers.append(int(m.group(1)))

    # 1b. duplicate numbers (the 0067 launcher/generation-parity collision class —
    # two ADRs issued the same number on parallel branches; resolved in 1.9.0 W1)
    seen: dict[int, str] = {}
    for name in adr_files:
        m = NAME_RE.match(name)
        if not m:
            continue
        num = int(m.group(1))
        if num in seen:
            errors.append(f"duplicate: ADR-{num:04d} used by both {seen[num]} and {name}")
        else:
            seen[num] = name

    # 2 + 3. index completeness / dead links
    index_text = INDEX.read_text(encoding="utf-8")
    linked = set(LINK_RE.findall(index_text))
    named = {n for n in adr_files if NAME_RE.match(n)}
    for missing in sorted(named - linked):
        errors.append(f"index: {missing} exists but is not linked from index.md")
    for dead in sorted(linked - named):
        errors.append(f"index: index.md links to missing file {dead}")

    # 4. per-file shape (H1 + Status)
    for name in sorted(named):
        text = (ADR_DIR / name).read_text(encoding="utf-8")
        m = H1_RE.search(text)
        if not m:
            errors.append(f"shape: {name} lacks an '# ADR-NNNN — Title' H1")
        elif m.group(1) != name[:4]:
            errors.append(f"shape: {name} H1 number ADR-{m.group(1)} != filename {name[:4]}")
        if not STATUS_RE.search(text):
            errors.append(f"shape: {name} lacks a Status line")

    # numbering gaps → warning only
    if numbers:
        expected = set(range(min(numbers), max(numbers) + 1))
        for gap in sorted(expected - set(numbers)):
            warnings.append(f"gap: ADR-{gap:04d} is missing (historical gaps are allowed)")

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"\n{len(named)} ADRs checked — {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
