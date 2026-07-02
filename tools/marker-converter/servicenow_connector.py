#!/usr/bin/env python3
"""
ServiceNow doc connector — Increment 1 (offline core).

PDF (ServiceNow module export) → structured, cited, wikilinked Markdown tree for the LLM wiki.

Pipeline
--------
1. Read the PDF bookmark outline (pypdfium2) → clean hierarchy + page numbers:
      L0 module ("IT Asset Management")  →  L1 feature ("Software Asset Management")
      →  L2 group ("Exploring …")        →  L3 section ("Software Asset Workspace")
2. For each section (bookmarks at --file-depth), convert ONLY its page range with Marker
   (Python API — models loaded once, reused for every section → fast, MPS-aware).
3. Clean the Marker markdown (collapse <br> in table cells, drop copyright/trademark
   boilerplate, merge "(continued)" tables, strip page-anchor spans, tidy headings).
4. Emit an LLM-wiki-ready page: YAML frontmatter (type/tool/module/feature/sources) +
   wikilink breadcrumb ([[ServiceNow]] › [[ITAM]] › [[SAM]]) + body + page citation footer.
5. Emit hub entity pages (ServiceNow → module → feature) so everything is linked in the graph.

Output goes to a STAGING dir (default tools/marker-converter/out/), NOT vault/raw/sources —
so the watcher doesn't ingest mid-build. Copy into the vault once the output looks right.

Run inside the Marker venv (has marker-pdf + pypdfium2 + torch). Example:
    TORCH_DEVICE=mps <venv>/bin/python servicenow_connector.py \
        --pdf ~/Downloads/servicenow-australia-it-asset-management-enus.pdf \
        --module-code ITAM --module-title "IT Asset Management" \
        --feature "Software Asset Management" \
        --sections "Software Asset Management overview,Software license metrics,Downgrade Rights"

Deterministic reference pages only (Increment 1). The per-feature LLM synthesis page
(hybrid) + registration + scheduler/UI + auto-download are later increments.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Domain maps (extend as more modules/features are added) ──────────────────────
MODULE_CODE: dict[str, str] = {
    "IT Asset Management": "ITAM",
    "IT Service Management": "ITSM",
    "Customer Service Management": "CSM",
}
FEATURE_CODE: dict[str, str] = {
    "Software Asset Management": "SAM",
    "Hardware Asset Management": "HAM",
    "Enterprise Asset Management": "EAM",
    "Cloud Asset Management": "CAM",
}

VENDOR = "ServiceNow"


@dataclass
class Bookmark:
    level: int
    title: str
    page_index: int  # 0-based


@dataclass
class Section:
    title: str
    module_title: str
    module_code: str
    feature_title: str
    feature_code: str
    group_title: str
    start_page: int  # 0-based, inclusive
    end_page: int  # 0-based, exclusive
    body: str = ""
    fields: dict[str, str] = field(default_factory=dict)


# ── Bookmarks ────────────────────────────────────────────────────────────────────
def read_bookmarks(pdf_path: Path) -> list[Bookmark]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    out: list[Bookmark] = []
    for b in pdf.get_toc():
        if b.page_index is None:
            continue
        out.append(Bookmark(level=b.level or 0, title=(b.title or "").strip(), page_index=b.page_index))
    return out


def page_count(pdf_path: Path) -> int:
    import pypdfium2 as pdfium

    return len(pypdfium2_doc := pdfium.PdfDocument(str(pdf_path)))  # noqa: F841


def select_sections(
    bookmarks: list[Bookmark],
    n_pages: int,
    *,
    module_title: str,
    module_code: str,
    feature_filter: str | None,
    group_filter: str | None,
    file_depth: int,
    section_names: list[str] | None,
) -> list[Section]:
    """Walk the outline, tracking ancestors, and materialize sections at file_depth."""
    # end page of each bookmark = next bookmark at level <= its level (exclusive)
    ends: list[int] = []
    for i, bm in enumerate(bookmarks):
        end = n_pages
        for j in range(i + 1, len(bookmarks)):
            if bookmarks[j].level <= bm.level:
                end = bookmarks[j].page_index
                break
        ends.append(max(end, bm.page_index + 1))

    sections: list[Section] = []
    stack: list[Bookmark] = []
    for i, bm in enumerate(bookmarks):
        while stack and stack[-1].level >= bm.level:
            stack.pop()
        ancestors = list(stack)
        stack.append(bm)

        if bm.level != file_depth:
            continue
        module = next((a for a in ancestors if a.level == 0), None)
        feature = next((a for a in ancestors if a.level == 1), None)
        group = next((a for a in ancestors if a.level == 2), None)
        if module is None or feature is None:
            continue
        if module.title != module_title:
            continue
        if feature_filter and feature.title != feature_filter:
            continue
        if group_filter and (group is None or group.title != group_filter):
            continue
        if section_names and bm.title not in section_names:
            continue

        f_code = FEATURE_CODE.get(feature.title, _slug(feature.title).upper()[:6])
        sections.append(
            Section(
                title=bm.title,
                module_title=module.title,
                module_code=module_code,
                feature_title=feature.title,
                feature_code=f_code,
                group_title=group.title if group else "",
                start_page=bm.page_index,
                end_page=ends[i],
            )
        )
    return sections


# ── Marker conversion (models loaded once) ───────────────────────────────────────
def make_converter_factory():  # noqa: ANN201 - marker types are dynamic
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    models = create_model_dict()

    def convert(pdf_path: Path, start_page: int, end_page: int) -> str:
        cfg = {"page_range": f"{start_page}-{end_page - 1}", "output_format": "markdown"}
        from marker.config.parser import ConfigParser

        parser = ConfigParser(cfg)
        converter = PdfConverter(
            config=parser.generate_config_dict(),
            artifact_dict=models,
            processor_list=parser.get_processors(),
            renderer=parser.get_renderer(),
        )
        rendered = converter(str(pdf_path))
        return getattr(rendered, "markdown", "") or ""

    return convert


# ── Cleanup ──────────────────────────────────────────────────────────────────────
_COPYRIGHT_PATTERNS = [
    re.compile(r"©\s*\d{4}\s*ServiceNow", re.I),
    re.compile(r"ServiceNow,\s*the\s*ServiceNow\s*logo", re.I),
    re.compile(r"Other\s+company\s+(names|and)", re.I),
    re.compile(r"trademarks?\s+and/or\s+registered\s+trademarks", re.I),
    re.compile(r"^\s*Company\s+Headquarters\s*$", re.I),
    re.compile(r"www\.servicenow\.com/terms-of-use", re.I),
]
_SPAN_RE = re.compile(r'<span\s+id="page-\d+-\d+"\s*>\s*</span>')
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")


def _is_boilerplate(line: str) -> bool:
    return any(p.search(line) for p in _COPYRIGHT_PATTERNS)


def clean_markdown(md: str) -> str:
    md = _SPAN_RE.sub("", md)
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # drop copyright/trademark boilerplate (incl. when promoted to a heading)
        stripped = line.lstrip("# ").strip()
        if _is_boilerplate(stripped):
            i += 1
            continue

        # normalize headings: strip bold + trailing hashes
        m = _HEADING_RE.match(line)
        if m:
            hashes, txt = m.group(1), m.group(2)
            txt = txt.strip().strip("*").strip()
            if not txt:
                i += 1
                continue
            # "(continued)" section: drop the heading and the repeated table header rows
            if txt.lower().endswith("(continued)"):
                i += 1
                # skip a following markdown table header + separator, if present
                if i < len(lines) and lines[i].lstrip().startswith("|"):
                    i += 1  # header row
                    if i < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|", lines[i]):
                        i += 1  # separator row
                continue
            out.append(f"{hashes} {txt}")
            i += 1
            continue

        # table rows: strip intra-cell HTML (Marker emits <br/>, <ul><li> in cells)
        if line.lstrip().startswith("|"):
            line = re.sub(r"<br\s*/?>", " ", line)          # <br>, <br/>, <br />
            line = re.sub(r"</li>\s*<li>", "; ", line)       # list items → "; "
            line = re.sub(r"</?(ul|ol|li)\s*>", " ", line)   # drop remaining list tags
            line = re.sub(r"[ \t]{2,}", " ", line)
        out.append(line)
        i += 1

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


# ── Page rendering ────────────────────────────────────────────────────────────────
def _slug(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "section"


def _page_label(start: int, end: int) -> str:
    a, b = start + 1, end  # 0-based [start,end) → 1-based inclusive
    return f"p.{a}" if b <= a + 1 else f"p.{a}–{b}"


def render_page(sec: Section, source_label: str, source_url: str | None) -> str:
    """
    Render ONE section as a raw SOURCE file for the normal Synapse ingest.

    Source-only by design: NO ``type`` (the ingest LLM classifies pages into valid wiki types —
    forcing e.g. ``type: reference`` breaks the type system), NO hub pages, NO forced
    ``[[wikilinks]]`` (linking to [[ServiceNow]]/modules is LLM-driven via the ingest context).
    We DO keep tool/module/feature as frontmatter *hints* (harmless — persist_metadata ignores
    unknown fields) and the page citation in the body so the LLM preserves provenance.
    """
    pages = _page_label(sec.start_page, sec.end_page)
    src = f"{source_label}, {pages}"
    if source_url:
        src += f" ({source_url})"
    fm = [
        "---",
        f'title: "{sec.title}"',
        f"tool: {VENDOR}",
        f"module: {sec.module_code}",
        f"feature: {sec.feature_code}",
    ]
    if sec.group_title:
        fm.append(f'group: "{sec.group_title}"')
    fm += [
        "sources:",
        f'  - "{src}"',
        f"tags: [servicenow, {sec.module_code.lower()}, {sec.feature_code.lower()}]",
        "---",
    ]
    # Plain-text context (NOT wikilinks) so the ingest LLM can link ServiceNow/module/feature.
    context = (
        f"> Source: {VENDOR} · {sec.module_title} ({sec.module_code}) "
        f"· {sec.feature_title} ({sec.feature_code})"
    )
    footer = f"---\n> **Fonte:** {source_label}, {pages}."
    return "\n".join(fm) + f"\n\n{context}\n\n# {sec.title}\n\n{sec.body}\n\n{footer}\n"


# ── Main ──────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="ServiceNow PDF → LLM-wiki markdown tree")
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    ap.add_argument("--module-title", default="IT Asset Management")
    ap.add_argument("--module-code", default="ITAM")
    ap.add_argument("--feature", default=None, help="Only this L1 feature title (e.g. 'Software Asset Management')")
    ap.add_argument("--group", default=None, help="Only this L2 group title (e.g. 'Exploring Software Asset Management')")
    ap.add_argument("--file-depth", type=int, default=3)
    ap.add_argument("--sections", default=None, help="Comma list of section titles to limit (demo)")
    ap.add_argument("--source-url", default="https://docs.servicenow.com")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    source_label = f"{VENDOR} Docs — {args.module_title} (Australia)"
    section_names = [s.strip() for s in args.sections.split(",")] if args.sections else None

    bookmarks = read_bookmarks(args.pdf)
    n_pages = page_count(args.pdf)
    sections = select_sections(
        bookmarks,
        n_pages,
        module_title=args.module_title,
        module_code=args.module_code,
        feature_filter=args.feature,
        group_filter=args.group,
        file_depth=args.file_depth,
        section_names=section_names,
    )
    if not sections:
        print("No sections matched — check --module-title/--feature/--file-depth.", file=sys.stderr)
        return 1

    print(f"Selected {len(sections)} section(s):")
    for s in sections:
        print(f"  [{s.module_code}/{s.feature_code}] {s.title}  ({_page_label(s.start_page, s.end_page)})")

    print("Loading Marker models (once)…")
    convert = make_converter_factory()

    base = args.out / "servicenow"

    for s in sections:
        print(f"→ converting {s.title} ({_page_label(s.start_page, s.end_page)}) …", flush=True)
        raw = convert(args.pdf, s.start_page, s.end_page)
        s.body = clean_markdown(raw)
        page = render_page(s, source_label, args.source_url)

        out_dir = base / s.module_code.lower() / s.feature_code.lower()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{_slug(s.title)}.md").write_text(page, encoding="utf-8")

    print(f"\nDone → {base}")
    print(
        "Sources written. To index them, place under vault/raw/sources/ and run the NORMAL "
        "ingest (watcher / POST /sources/ingest-all) — the LLM assigns wiki types + links."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
