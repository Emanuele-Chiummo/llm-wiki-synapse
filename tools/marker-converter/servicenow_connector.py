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
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── Domain maps (extend as more modules/features are added) ──────────────────────
# Known modules keep a curated short code; --auto derives one from the outline for any
# module not listed here (so a brand-new book needs zero pre-configuration).
MODULE_CODE: dict[str, str] = {
    "IT Asset Management": "ITAM",
    "IT Service Management": "ITSM",
    "IT Operations Management": "ITOM",
    "Customer Service Management": "CSM",
}
FEATURE_CODE: dict[str, str] = {
    "Software Asset Management": "SAM",
    "Hardware Asset Management": "HAM",
    "Enterprise Asset Management": "EAM",
    "Cloud Asset Management": "CAM",
}

VENDOR = "ServiceNow"


def _derive_code(title: str, known: dict[str, str]) -> str:
    """
    Return a short UPPERCASE code for a module/feature title.

    Precedence: curated map → acronym of the significant words (e.g.
    "IT Operations Management" → "ITOM") → slug fallback. Used by --auto so any
    ServiceNow book splits without hand-maintained domain maps.
    """
    if title in known:
        return known[title]
    words = [w for w in re.split(r"[^A-Za-z0-9]+", title) if w]
    if 2 <= len(words) <= 8:
        acronym = "".join(w[0] for w in words).upper()
        if 2 <= len(acronym) <= 8:
            return acronym
    return _slug(title).upper()[:6] or "DOC"


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
        out.append(
            Bookmark(
                level=b.level or 0,
                title=(b.title or "").strip(),
                page_index=b.page_index,
            )
        )
    return out


def page_count(pdf_path: Path) -> int:
    import pypdfium2 as pdfium

    return len(pypdfium2_doc := pdfium.PdfDocument(str(pdf_path)))  # noqa: F841


def select_sections(
    bookmarks: list[Bookmark],
    n_pages: int,
    *,
    module_title: str | None,
    module_code: str,
    feature_filter: str | None,
    group_filter: str | None,
    file_depth: int,
    section_names: list[str] | None,
) -> list[Section]:
    """
    Walk the outline, tracking ancestors, and materialize sections at file_depth.

    Auto mode: pass ``module_title=None`` to accept EVERY module in the book (no ITAM
    preset). Each section's ``module_code`` is then derived from its own L0 title via
    ``_derive_code`` (curated map → acronym → slug), so a brand-new module such as ITOM
    splits with zero configuration. When ``module_title`` is a concrete string, only that
    module is materialized and the supplied ``module_code`` is used verbatim (legacy path).
    """
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
        if module_title is not None and module.title != module_title:
            continue
        if feature_filter and feature.title != feature_filter:
            continue
        if group_filter and (group is None or group.title != group_filter):
            continue
        if section_names and bm.title not in section_names:
            continue

        # Auto mode (module_title is None): derive the code from each section's own
        # module title so any book splits without a curated map. Legacy mode keeps the
        # explicit --module-code the caller passed.
        m_code = (
            module_code
            if module_title is not None
            else _derive_code(module.title, MODULE_CODE)
        )
        f_code = _derive_code(feature.title, FEATURE_CODE)
        sections.append(
            Section(
                title=bm.title,
                module_title=module.title,
                module_code=m_code,
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
        cfg = {
            "page_range": f"{start_page}-{end_page - 1}",
            "output_format": "markdown",
        }
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
            line = re.sub(r"<br\s*/?>", " ", line)  # <br>, <br/>, <br />
            line = re.sub(r"</li>\s*<li>", "; ", line)  # list items → "; "
            line = re.sub(r"</?(ul|ol|li)\s*>", " ", line)  # drop remaining list tags
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


# ── Daemon / watch-dir mode (R7-7, AC-R7-7-1..R7-7-3) ───────────────────────────
#
# The watch-dir daemon scans a local directory for new PDF files on a configurable
# interval, converts them with Marker, and drops the resulting .md files into the
# configured output directory so Synapse's watcher / import-schedule auto-ingests them.
#
# Bounds (I7-style, AC-R7-7-2):
#   - Max 20 PDFs per tick (MAX_PDFS_PER_TICK).
#   - SHA-256 hash gate: PDFs already converted are skipped (I1 spirit).
#   - Logs total_files_converted and total_cost_usd=0.00 per tick (Marker is local).
#
# State is persisted in a small JSON file alongside the output dir so converted
# hashes survive restarts.
#
# Auto-download stub (AC-R7-7-4, experimental):
#   When SERVICENOW_AUTODOWNLOAD_EXPERIMENTAL=1, a warning is logged and the function
#   exits immediately (not implemented). This path is never reached by the normal
#   daemon loop and is not gate-blocking.

_STATE_FILE_NAME = ".sn_connector_state.json"
MAX_PDFS_PER_TICK = 20  # I7 cap (AC-R7-7-2)


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file (hash gate for I1-style deduplication)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_state(state_path: Path) -> dict[str, str]:
    """Load the conversion state dict {sha256 → output_path} from JSON, or {} if absent."""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state_path: Path, state: dict[str, str]) -> None:
    """Persist the conversion state dict atomically."""
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def _auto_download_stub() -> None:
    """
    Auto-download stub (AC-R7-7-4 — experimental, NOT gate-blocking).

    When SERVICENOW_AUTODOWNLOAD_EXPERIMENTAL=1 this function is called and logs a
    clear warning. It does NOT scrape docs.servicenow.com. Provide PDFs manually
    in the watch dir instead.
    """
    print(
        "[WARN] SERVICENOW_AUTODOWNLOAD_EXPERIMENTAL=1 detected. "
        "Auto-download is NOT implemented — provide PDFs manually in the watch dir. "
        "This stub exits immediately without downloading anything.",
        file=sys.stderr,
    )


def run_watch_tick(
    watch_dir: Path,
    out_dir: Path,
    *,
    module_title: str = "IT Asset Management",
    module_code: str = "ITAM",
    source_url: str = "https://docs.servicenow.com",
    file_depth: int = 3,
    auto: bool = False,
    max_per_tick: int = MAX_PDFS_PER_TICK,
    convert_fn: object = None,  # callable(pdf, start, end) -> str; injected in tests
) -> dict[str, int]:
    """
    One scheduler tick: find NEW PDFs in watch_dir, convert up to max_per_tick, drop
    resulting .md files into out_dir/servicenow/…, update the state file.

    Returns {"total_files_converted": N, "total_cost_usd": 0} (Marker is local).

    Args:
        watch_dir:    Local directory to scan for PDF files (AC-R7-7-1).
        out_dir:      Directory where converted .md files are written (picked up by watcher).
        module_title: ServiceNow module name for frontmatter/path (default ITAM).
        module_code:  Short module code for output path (default ITAM).
        source_url:   Source URL for page citations.
        file_depth:   Bookmark depth for section splitting (default 3).
        max_per_tick: Max PDFs to convert per tick (I7 cap, AC-R7-7-2).
        convert_fn:   Optional converter callable; if None, Marker is loaded lazily on first PDF.

    Logs total_files_converted and total_cost_usd=0.00 per tick (I7, AC-R7-7-2).
    """
    state_path = out_dir / _STATE_FILE_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    state = _load_state(state_path)

    pdfs = sorted(watch_dir.glob("*.pdf"))
    new_pdfs = [p for p in pdfs if _sha256_file(p) not in state]

    if not new_pdfs:
        print(f"[tick] No new PDFs in {watch_dir} — skipping.")
        return {"total_files_converted": 0, "total_cost_usd": 0}

    # Apply I7 cap (AC-R7-7-2)
    capped = new_pdfs[:max_per_tick]
    if len(new_pdfs) > max_per_tick:
        print(
            f"[tick] {len(new_pdfs)} new PDFs found; capping at {max_per_tick} per tick (I7).",
            file=sys.stderr,
        )

    # Lazy-load Marker converter on first PDF (not in __init__ so tests can inject a stub)
    _convert = convert_fn
    converted = 0

    for pdf_path in capped:
        sha = _sha256_file(pdf_path)
        print(f"[tick] Converting {pdf_path.name} …", flush=True)
        try:
            # Read bookmarks from this PDF; if unreadable (no Marker/pypdfium2), skip cleanly.
            try:
                bms = read_bookmarks(pdf_path)
                n_pages = page_count(pdf_path)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[tick] WARNING: could not read bookmarks for {pdf_path.name}: {exc}",
                    file=sys.stderr,
                )
                # Mark as "seen" with a sentinel so we don't retry endlessly on a bad PDF.
                state[sha] = f"error:{pdf_path.name}"
                _save_state(state_path, state)
                continue

            sections = select_sections(
                bms,
                n_pages,
                module_title=None if auto else module_title,
                module_code=module_code,
                feature_filter=None,
                group_filter=None,
                file_depth=file_depth,
                section_names=None,
            )
            if not sections:
                print(
                    f"[tick] No sections matched in {pdf_path.name} — skipping body.",
                    file=sys.stderr,
                )
                # Still mark as seen to avoid repeated futile re-processing.
                state[sha] = f"no_sections:{pdf_path.name}"
                _save_state(state_path, state)
                continue

            # Lazy-load Marker converter (only if we have real sections to convert)
            if _convert is None:
                print("[tick] Loading Marker models (once) …")
                _convert = make_converter_factory()

            base = out_dir / "servicenow"
            written_paths: list[str] = []

            for sec in sections:
                # Per-section label so --auto books that span multiple L0 modules cite the
                # correct module; legacy single-module runs resolve to the same string.
                source_label = f"{VENDOR} Docs — {sec.module_title}"
                raw = _convert(pdf_path, sec.start_page, sec.end_page)  # type: ignore[operator]
                sec.body = clean_markdown(raw)
                page_text = render_page(sec, source_label, source_url)

                sec_dir = base / sec.module_code.lower() / sec.feature_code.lower()
                sec_dir.mkdir(parents=True, exist_ok=True)
                out_file = sec_dir / f"{_slug(sec.title)}.md"
                out_file.write_text(page_text, encoding="utf-8")
                written_paths.append(str(out_file))

            state[sha] = f"converted:{pdf_path.name}:{len(sections)}sections"
            _save_state(state_path, state)
            converted += 1
            print(
                f"[tick] {pdf_path.name} → {len(sections)} section(s) written to {base}",
                flush=True,
            )

        except Exception as exc:  # noqa: BLE001
            print(f"[tick] ERROR converting {pdf_path.name}: {exc}", file=sys.stderr)
            # Do NOT mark as seen — allow retry next tick for transient failures.

    result = {"total_files_converted": converted, "total_cost_usd": 0}
    print(
        f"[tick] Done — total_files_converted={converted}, total_cost_usd=0.00 (Marker is local)"
    )
    return result


def watch_daemon(
    watch_dir: Path,
    out_dir: Path,
    interval_minutes: int,
    *,
    module_title: str = "IT Asset Management",
    module_code: str = "ITAM",
    source_url: str = "https://docs.servicenow.com",
    file_depth: int = 3,
    auto: bool = False,
) -> None:
    """
    Run the watch-dir daemon: tick every interval_minutes, bounded at MAX_PDFS_PER_TICK
    per tick (I7, AC-R7-7-2). Runs forever until interrupted (SIGINT/SIGTERM).

    Each tick calls run_watch_tick(); converted .md files land in out_dir/servicenow/…
    so Synapse's watcher / import-schedule auto-ingests them (AC-R7-7-1).
    """
    interval_seconds = max(60, interval_minutes * 60)
    print(
        f"[daemon] Starting ServiceNow connector daemon: "
        f"watch_dir={watch_dir} out_dir={out_dir} interval={interval_minutes}m"
    )

    # Auto-download experimental stub (AC-R7-7-4)
    if os.environ.get("SERVICENOW_AUTODOWNLOAD_EXPERIMENTAL") == "1":
        _auto_download_stub()

    while True:
        print(f"[daemon] Tick at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            run_watch_tick(
                watch_dir,
                out_dir,
                module_title=module_title,
                module_code=module_code,
                source_url=source_url,
                file_depth=file_depth,
                auto=auto,
            )
        except KeyboardInterrupt:
            print("[daemon] Interrupted — stopping.")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"[daemon] ERROR during tick: {exc}", file=sys.stderr)

        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("[daemon] Interrupted during sleep — stopping.")
            return


# ── Main ──────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "ServiceNow PDF → LLM-wiki markdown tree.\n\n"
            "Single-PDF mode (default): --pdf <file> converts one PDF.\n"
            "Daemon mode: --watch-dir <dir> --out <dir> --interval-minutes N\n"
            "  watches a local folder for new PDFs and converts them on a schedule.\n"
            "  Converted .md files land in <out>/servicenow/ for Synapse auto-ingest.\n\n"
            "Auto-download (experimental, not gate-blocking):\n"
            "  Set SERVICENOW_AUTODOWNLOAD_EXPERIMENTAL=1 to see the stub warning.\n"
            "  Actual download from docs.servicenow.com is NOT implemented — provide PDFs manually."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # ── Daemon / watch-dir mode arguments (R7-7) ────────────────────────────────
    ap.add_argument(
        "--watch-dir",
        type=Path,
        default=None,
        help="Directory to watch for new PDFs (daemon mode, R7-7). Enables --interval-minutes.",
    )
    ap.add_argument(
        "--interval-minutes",
        type=int,
        default=60,
        help="Tick interval in minutes for daemon mode (default 60, min 1).",
    )
    ap.add_argument(
        "--auto-download",
        action="store_true",
        default=False,
        help=(
            "[EXPERIMENTAL] Enable auto-download stub (requires SERVICENOW_AUTODOWNLOAD_EXPERIMENTAL=1). "
            "Currently logs a warning and does nothing — do NOT rely on this flag."
        ),
    )
    # ── Single-PDF mode arguments (original) ───────────────────────────────────
    ap.add_argument(
        "--pdf", type=Path, default=None, help="PDF to convert (single-PDF mode)"
    )
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    ap.add_argument("--module-title", default="IT Asset Management")
    ap.add_argument("--module-code", default="ITAM")
    ap.add_argument(
        "--feature",
        default=None,
        help="Only this L1 feature title (e.g. 'Software Asset Management')",
    )
    ap.add_argument(
        "--group",
        default=None,
        help="Only this L2 group title (e.g. 'Exploring Software Asset Management')",
    )
    ap.add_argument(
        "--auto",
        action="store_true",
        default=False,
        help=(
            "Auto mode: derive module/feature codes from the PDF outline instead of the "
            "curated maps (no --module-title/--feature preset needed). Splits EVERY module in "
            "the book. Pairs with --file-depth (defaults to 2 = one file per L2 chapter/group "
            "in --auto). Ideal for large books dropped into --watch-dir."
        ),
    )
    ap.add_argument(
        "--file-depth",
        type=int,
        default=None,
        help="Bookmark level to split at (L0=0…L3=3). Default: 2 with --auto, else 3.",
    )
    ap.add_argument(
        "--sections", default=None, help="Comma list of section titles to limit (demo)"
    )
    ap.add_argument("--source-url", default="https://docs.servicenow.com")
    args = ap.parse_args()

    # Resolve split depth: --auto splits at L2 (one file per chapter/group) by default;
    # legacy mode keeps L3 sections. An explicit --file-depth always wins.
    file_depth = (
        args.file_depth if args.file_depth is not None else (2 if args.auto else 3)
    )

    # ── Daemon mode ────────────────────────────────────────────────────────────
    if args.watch_dir is not None:
        if not args.watch_dir.is_dir():
            print(f"Watch directory not found: {args.watch_dir}", file=sys.stderr)
            return 2
        if (
            args.auto_download
            and os.environ.get("SERVICENOW_AUTODOWNLOAD_EXPERIMENTAL") == "1"
        ):
            _auto_download_stub()
        watch_daemon(
            watch_dir=args.watch_dir,
            out_dir=args.out,
            interval_minutes=max(1, args.interval_minutes),
            module_title=args.module_title,
            module_code=args.module_code,
            source_url=args.source_url,
            file_depth=file_depth,
            auto=args.auto,
        )
        return 0

    # ── Single-PDF mode (original behaviour) ──────────────────────────────────
    if args.pdf is None:
        print(
            "Error: --pdf is required in single-PDF mode (or use --watch-dir for daemon mode).",
            file=sys.stderr,
        )
        return 2
    if not args.pdf.exists():
        print(f"PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    section_names = (
        [s.strip() for s in args.sections.split(",")] if args.sections else None
    )

    bookmarks = read_bookmarks(args.pdf)
    n_pages = page_count(args.pdf)
    sections = select_sections(
        bookmarks,
        n_pages,
        module_title=None if args.auto else args.module_title,
        module_code=args.module_code,
        feature_filter=args.feature,
        group_filter=args.group,
        file_depth=file_depth,
        section_names=section_names,
    )
    if not sections:
        hint = (
            "No sections matched — the PDF outline may be flat or unreadable."
            if args.auto
            else "No sections matched — check --module-title/--feature/--file-depth (or try --auto)."
        )
        print(hint, file=sys.stderr)
        return 1

    print(f"Selected {len(sections)} section(s):")
    for s in sections:
        print(
            f"  [{s.module_code}/{s.feature_code}] {s.title}  ({_page_label(s.start_page, s.end_page)})"
        )

    print("Loading Marker models (once)…")
    convert = make_converter_factory()

    base = args.out / "servicenow"

    for s in sections:
        print(
            f"→ converting {s.title} ({_page_label(s.start_page, s.end_page)}) …",
            flush=True,
        )
        raw = convert(args.pdf, s.start_page, s.end_page)
        s.body = clean_markdown(raw)
        source_label = f"{VENDOR} Docs — {s.module_title} (Australia)"
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
