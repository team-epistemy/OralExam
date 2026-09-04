"""
Syllabus -> Session Mapper
==========================

Pipeline:
  1. Sweep the document to find the start/end of the class-schedule section,
     trying several independent marker strategies (a schedule stays findable
     even if it has no explicit heading, or lives in a real PDF table).
  2. Parse a window of content near the start into
     <Date, Topic, Reading, Case Study, Article, Documents, Misc> tuples.
  3. Slide forward and confirm the <date, topic> pattern actually repeats,
     so a single stray date in prose (e.g. "within 72 hours") never gets
     mistaken for a schedule.
  4. Parse everything between start/end into 'n' tuples.
  5. Validate every tuple individually. Tuples that fail are quarantined
     (kept, with reasons) rather than silently dropped or failing the whole
     batch — a document with 8 clean rows and 1 malformed row still succeeds.
  6. If, after 3 attempts (each trying a different marker strategy), the
     batch still doesn't clear the structural bar, surface
     'Failed to Parse syllabus document'.

Extraction is column-aware: pages are checked for a persistent vertical
gutter (a newspaper-style 2-column layout). If found, each column is read
top-to-bottom independently and concatenated left-then-right, instead of
interleaving lines across columns by raw y-position (which corrupts tuples
on multi-column syllabi).
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field, asdict
from typing import Optional

# pdfplumber is imported lazily inside the two functions that open the PDF, so
# this module (and the tuple→session mapping below) stays importable and
# unit-testable in environments without the heavy PDF stack installed.


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Line:
    page: int
    line_no: int          # index within the whole document (0-based)
    text: str


@dataclass
class SessionTuple:
    date: Optional[str] = None
    topic: Optional[str] = None
    reading: Optional[str] = None          # textbook / lecture-note references
    case_study: Optional[str] = None       # items explicitly marked "(Case)"
    article: Optional[str] = None          # newspaper/magazine citations
    documents: Optional[str] = None        # due dates, submissions, deliverables
    misc: Optional[str] = None             # everything else
    source_lines: list = field(default_factory=list)   # doc line indices, for traceability

    def is_minimally_valid(self) -> bool:
        return bool(self.date) and bool(self.topic)


@dataclass
class ValidationReport:
    valid: list                      # list[SessionTuple]
    quarantined: list                # list[(SessionTuple, list[str] reasons)]
    structural_failures: list        # list[str] — batch-level problems

    @property
    def ok(self) -> bool:
        return not self.structural_failures and len(self.valid) > 0


@dataclass
class ParseResult:
    success: bool
    strategy: str
    attempts: int
    start_marker: Optional[int]
    end_marker: Optional[int]
    tuples: list                     # valid tuples, as dicts
    quarantined: list                # [{tuple, reasons}], as dicts
    failures: list                   # attempt-by-attempt failure log
    message: str


# ---------------------------------------------------------------------------
# Step 0: column-aware extraction
# ---------------------------------------------------------------------------

def _group_words_into_rows(words: list, y_tol: float = 3.0,
                            gap_threshold: float = 12.0) -> list[dict]:
    """Group words into text-row segments.

    Words are first clustered by y-proximity (same text line). Critically,
    within one y-band we then split on large horizontal gaps: two words that
    sit on the same visual line but are separated by a wide gutter (left
    column's last word, right column's first word) are NOT the same row —
    grouping purely by y merges them into one line spanning the whole page
    width, which silently defeats column detection before it can even run.
    A gap wider than `gap_threshold` (well beyond ordinary inter-word
    spacing) starts a new segment instead."""
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))
    y_groups = []
    current, current_top = [], None
    for w in words_sorted:
        if current_top is None or abs(w["top"] - current_top) <= y_tol:
            current.append(w)
            current_top = current_top if current_top is not None else w["top"]
        else:
            y_groups.append(current)
            current, current_top = [w], w["top"]
    if current:
        y_groups.append(current)

    row_infos = []
    for group in y_groups:
        group_sorted = sorted(group, key=lambda w: w["x0"])
        segments = [[group_sorted[0]]]
        for prev, w in zip(group_sorted, group_sorted[1:]):
            if w["x0"] - prev["x1"] > gap_threshold:
                segments.append([w])
            else:
                segments[-1].append(w)
        for seg in segments:
            row_infos.append({
                "top": min(w["top"] for w in seg),
                "x0": min(w["x0"] for w in seg),
                "x1": max(w["x1"] for w in seg),
                "text": " ".join(w["text"] for w in seg),
            })
    row_infos.sort(key=lambda r: (r["top"], r["x0"]))
    return row_infos


def _detect_column_gutter(row_infos: list, page_width: float,
                           min_gap_frac: float = 0.04, span_frac: float = 0.6
                           ) -> Optional[tuple[float, float]]:
    """Look for a vertical strip with zero row-coverage, roughly centered
    (not near the margins). Rows that already span most of the page's
    content width (titles, section headers sitting above two columns) are
    excluded from the occupancy scan — otherwise a single full-width title
    line fills the would-be gutter and column detection never fires."""
    if not row_infos:
        return None
    all_x0 = min(r["x0"] for r in row_infos)
    all_x1 = max(r["x1"] for r in row_infos)
    content_width = all_x1 - all_x0
    if content_width <= 0:
        return None

    narrow_rows = [r for r in row_infos if (r["x1"] - r["x0"]) <= span_frac * content_width]
    if len(narrow_rows) < 2:
        return None  # not enough column-shaped rows to trust a gutter

    bins = 200
    bin_width = page_width / bins
    occupied = [False] * bins
    for r in narrow_rows:
        b0 = max(0, int(r["x0"] / bin_width))
        b1 = min(bins - 1, int(r["x1"] / bin_width))
        for b in range(b0, b1 + 1):
            occupied[b] = True

    gap_runs = []
    i = 0
    while i < bins:
        if not occupied[i]:
            j = i
            while j < bins and not occupied[j]:
                j += 1
            gap_runs.append((i, j))
            i = j
        else:
            i += 1

    best = None
    for (i, j) in gap_runs:
        gap_frac = (j - i) / bins
        center = (i + j) / 2 / bins
        if gap_frac >= min_gap_frac and 0.25 < center < 0.75:
            if best is None or gap_frac > best[2]:
                best = (i, j, gap_frac)
    if best is None:
        return None
    return best[0] * bin_width, best[1] * bin_width


def _words_to_lines_simple(words: list, y_tol: float = 3.0) -> list[str]:
    """Group words into lines by y-proximity only — no horizontal-gap
    splitting. Used when no column gutter was detected on a page, so a
    hanging-indent bullet's wide gap before its own text (e.g. a lone '-'
    marker with the bullet text starting well to its right) doesn't get
    mistaken for a column split by _group_words_into_rows's gap heuristic
    and torn into two separate lines — that heuristic exists to help find
    genuine gutters, and shouldn't apply once we know there isn't one."""
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: (w["top"], w["x0"]))
    rows = []
    current, current_top = [], None
    for w in words_sorted:
        if current_top is None or abs(w["top"] - current_top) <= y_tol:
            current.append(w)
            current_top = current_top if current_top is not None else w["top"]
        else:
            rows.append(current)
            current, current_top = [w], w["top"]
    if current:
        rows.append(current)
    out = []
    for row in rows:
        row_sorted = sorted(row, key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in row_sorted)
        top = min(w["top"] for w in row_sorted)
        out.append((top, text))
    out.sort(key=lambda t: t[0])
    return [t for _, t in out]


def extract_lines(pdf_path: str) -> list[Line]:
    """Flatten the PDF into a single ordered list of non-empty lines.

    Column-aware: rows are grouped, a gutter is detected (if present) from
    non-spanning rows only, and then rows are walked top-to-bottom. Any row
    that itself crosses the gutter (a title, a section header) flushes the
    buffered left/right column content immediately before it and is emitted
    on its own — so a header sitting above two columns doesn't get stitched
    mid-sentence into either column, and doesn't block gutter detection for
    the columns below it."""
    import pdfplumber
    lines: list[Line] = []
    idx = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages):
            words = page.extract_words()
            if not words:
                text = page.extract_text() or ""
                for raw in text.split("\n"):
                    stripped = raw.strip()
                    if stripped:
                        lines.append(Line(page=page_no, line_no=idx, text=stripped))
                        idx += 1
                continue

            row_infos = _group_words_into_rows(words)
            gutter = _detect_column_gutter(row_infos, page.width)

            if not gutter:
                for text in _words_to_lines_simple(words):
                    lines.append(Line(page=page_no, line_no=idx, text=text))
                    idx += 1
                continue

            gx0, gx1 = gutter
            gcenter = (gx0 + gx1) / 2
            left_buf: list[str] = []
            right_buf: list[str] = []

            def flush():
                nonlocal idx
                for t in left_buf:
                    lines.append(Line(page=page_no, line_no=idx, text=t))
                    idx += 1
                for t in right_buf:
                    lines.append(Line(page=page_no, line_no=idx, text=t))
                    idx += 1
                left_buf.clear()
                right_buf.clear()

            for r in row_infos:
                crosses_gutter = r["x0"] < gx0 + 1 and r["x1"] > gx1 - 1
                if crosses_gutter:
                    flush()
                    lines.append(Line(page=page_no, line_no=idx, text=r["text"]))
                    idx += 1
                else:
                    center = (r["x0"] + r["x1"]) / 2
                    (left_buf if center < gcenter else right_buf).append(r["text"])
            flush()
    return lines


# ---------------------------------------------------------------------------
# Marker detection strategies
# ---------------------------------------------------------------------------

DATE_PATTERNS = [
    r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b",
    r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+\w+\s+\d{1,2}(st|nd|rd|th)?\b",
    r"\b(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2}(st|nd|rd|th)?\b",
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2}(st|nd|rd|th)?\b",
]
SESSION_PATTERNS = [
    r"\bWeek\s*\d+\b",
    r"\bSession\s*\d+\b",
    r"\bClass\s*\d+\b",
    r"\bLecture\s*\d+\b",
    r"\bModule\s*\d+\b",
    r"\bBlock\s*\d+\b",
]
SCHEDULE_HEADER_HINTS = [
    "course schedule", "class schedule", "session schedule", "calendar",
    "weekly schedule", "course calendar", "course outline", "agenda",
    "readings schedule", "lecture schedule", "detailed schedule",
    "topics, readings, and assignment due dates", "topics and readings",
]
END_SECTION_HINTS = [
    "grading", "grade dispute", "honor code", "academic integrity",
    "accommodation", "resources", "policies", "appendix",
    "assignments and other deliverables", "problem sets", "class participation",
]

_date_re = re.compile("|".join(DATE_PATTERNS), re.IGNORECASE)
_session_re = re.compile("|".join(SESSION_PATTERNS), re.IGNORECASE)


def _looks_like_heading(text: str, hint: str, max_len: int = 60, min_ratio: float = 0.3) -> bool:
    """A hint phrase appearing SOMEWHERE in a line isn't enough to trust it
    as a section heading — syllabi routinely reference "the class schedule"
    or "additional resources" in ordinary prose, paragraphs away from the
    actual section. Require the line to be short and to be MOSTLY the hint
    phrase (not a phrase buried inside a longer sentence), which is what
    distinguishes a real heading like 'Course Schedule' or 'UC Berkeley
    Resources' from 'See the class schedule for relevant chapters.'"""
    stripped = text.strip()
    if not stripped or len(stripped) > max_len:
        return False
    return len(hint) / len(stripped) >= min_ratio

# Strip a leading list marker (bullet, or "12. " / "12) " numbering) before
# checking whether a line *opens* with a date/session marker. Anchoring the
# check to the start of the line (rather than searching anywhere in it) is
# what keeps citation dates buried inside reading bullets — e.g. "Wall
# Street Journal, 7/21/2023" — from being mistaken for schedule rows. A
# generic anywhere-in-line search treats those identically to a real
# "12. 10/3: Price & Quantity Competition" header, and on reading-heavy
# syllabi the citation dates usually outnumber the real ones.
_LEADING_MARKER_RE = re.compile(r"^\s*(?:[\u2022\-\*]\s+|\(?\d{1,2}\)?[.)]\s+)?")


def _line_is_schedule_row(text: str) -> bool:
    stripped = _LEADING_MARKER_RE.sub("", text, count=1)

    # Session-keyword markers ("Week 3", "Module II") are unambiguous even
    # with nothing else on the line.
    if _session_re.match(stripped):
        return True

    # A bare date is not: PDF text-wrapping frequently strands a citation's
    # trailing date alone on its own line (e.g. a bullet reading "...Wall
    # Street Journal," wraps, and "11/10/2022" becomes the entire next
    # line). That solo date sits at position 0 just like a real schedule
    # marker would, so the anchor check alone doesn't catch it. Require
    # actual topic text to follow the date on the same line.
    date_match = _date_re.match(stripped)
    if not date_match:
        return False
    remainder = stripped[date_match.end():].strip(" -:\u2013\u2014.,")
    return len(remainder) >= 3


def strategy_header_anchored(lines: list[Line]) -> tuple[Optional[int], Optional[int]]:
    header_idx = None
    for i, ln in enumerate(lines):
        low = ln.text.lower()
        for hint in SCHEDULE_HEADER_HINTS:
            if hint in low and _looks_like_heading(ln.text, hint):
                header_idx = i
                break
        if header_idx is not None:
            break
    if header_idx is None:
        return None, None
    return _bound_schedule_run(lines, search_from=header_idx + 1)


def strategy_pattern_sweep(lines: list[Line]) -> tuple[Optional[int], Optional[int]]:
    """Cluster schedule-row candidates into a contiguous run.

    The gap tolerance used to be 4 lines, back when _line_is_schedule_row
    was a much noisier anywhere-in-line date search. Since then it's been
    hardened considerably (anchored at line-start, requires real topic
    text or a nearby date for session keywords, rejects bare citation
    dates) — false-positive candidates are now rare enough that a tight
    gap tolerance does more harm than good: real narrative syllabi
    routinely have 10-30 lines of reading list between one session header
    and the next, which a 4-line tolerance fragments into disconnected
    single-line 'runs' that all get rejected as too short to trust.
    """
    candidates = [i for i, ln in enumerate(lines) if _line_is_schedule_row(ln.text)]
    if not candidates:
        return None, None
    runs = []
    run_start = candidates[0]
    prev = candidates[0]
    for c in candidates[1:]:
        if c - prev > 40:
            runs.append((run_start, prev))
            run_start = c
        prev = c
    runs.append((run_start, prev))
    runs.sort(key=lambda r: r[1] - r[0], reverse=True)
    best_start, best_end = runs[0]
    if best_end - best_start < 1:
        return None, None
    return best_start, _extend_through_trailing_content(lines, best_end)


def _cell_is_schedule_like(cell: Optional[str]) -> bool:
    """Looser than _line_is_schedule_row on purpose: a table cell is already
    isolated to one logical unit by the table structure, so there's much
    less risk of matching a stray citation date buried in a sentence — an
    anywhere-in-cell search is safe here, and necessary, since real-world
    schedule tables often format the cell as '1: May 15' or '2: June 6',
    which doesn't match a schedule-row line pattern anchored at position 0."""
    if not cell:
        return False
    flat = cell.replace("\n", " ")
    return bool(_date_re.search(flat) or _session_re.search(flat))


def _clean_cell(cell: Optional[str]) -> Optional[str]:
    if cell is None:
        return None
    text = re.sub(r"\s+", " ", cell.replace("\n", " ")).strip()
    return text or None


def _parse_table_schedule(table: list) -> list[SessionTuple]:
    """Build tuples directly from a real PDF table's columns, using the
    header row's wording (Topic / Reading / Textbook / Submissions ...) to
    decide which column feeds which field, rather than guessing from
    concatenated cell text. Falls back to 'the column right after the date
    column' for topic if no header says 'topic' explicitly. Rows whose date
    column doesn't look schedule-like are skipped — typically block-level
    deadline reminders that don't belong to one specific session."""
    if not table or len(table) < 2:
        return []
    ncols = max(len(r) for r in table)
    header_texts = [(_clean_cell(h) or "").lower() for h in table[0]] + [""] * (ncols - len(table[0]))
    data_rows = table[1:]

    col_hits = [0] * ncols
    for row in data_rows:
        for ci, cell in enumerate(row):
            if _cell_is_schedule_like(cell):
                col_hits[ci] += 1
    if max(col_hits, default=0) < 2:
        return []
    date_col = col_hits.index(max(col_hits))

    def find_col(keywords):
        for ci, h in enumerate(header_texts):
            if any(k in h for k in keywords):
                return ci
        return None

    topic_col = find_col(["topic"])
    if topic_col is None and date_col + 1 < ncols:
        topic_col = date_col + 1
    content_cols = [ci for ci, h in enumerate(header_texts)
                     if any(k in h for k in ("reading", "case", "textbook", "submission", "due"))
                     and ci not in (date_col, topic_col)]

    tuples: list[SessionTuple] = []
    for row in data_rows:
        row = list(row) + [None] * (ncols - len(row))
        if not _cell_is_schedule_like(row[date_col]):
            continue
        flat_date_cell = (row[date_col] or "").replace("\n", " ")
        date_match = _date_re.search(flat_date_cell) or _session_re.search(flat_date_cell)
        date_str = date_match.group(0) if date_match else _clean_cell(row[date_col])

        t = SessionTuple(date=date_str, source_lines=[])
        # Process the topic column FIRST (not as a direct blob assignment):
        # some syllabi table layouts have a single combined "Topics" column
        # that mixes genuine topic bullets with case citations and guest-
        # speaker lines. Assigning that whole cell straight to `topic`
        # produces an implausibly long, mis-classified blob. Running it
        # through the same merge+classify path as the other columns lets
        # case/article/documents lines get routed correctly, while
        # processing it first ensures genuine topic text still claims the
        # topic slot before other columns get a chance to.
        ordered_cols = ([topic_col] if topic_col is not None else []) + content_cols
        for ci in ordered_cols:
            raw_cell = row[ci] if ci < len(row) else None
            if not raw_cell:
                continue
            for item in _merge_wrapped_items(raw_cell.split("\n")):
                cleaned = _clean_cell(item)
                if cleaned:
                    _classify_into(t, cleaned)
        tuples.append(t)
    return tuples


def extract_table_schedule(pdf_path: str) -> list[SessionTuple]:
    """Scan every table on every page; keep whichever produces the most
    tuples. A syllabus with both a narrative schedule and a clean summary
    table (common — the table is usually written to be more parseable)
    should prefer the table."""
    import pdfplumber
    best: list[SessionTuple] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                tuples = _parse_table_schedule(table)
                if len(tuples) > len(best):
                    best = tuples
    return best


_SECTION_HEADING_RE = re.compile(r"^\d+(\.\d)?\s+[A-Z][a-z]")


def _extend_through_trailing_content(lines: list[Line], from_line: int) -> int:
    """Walk forward from a known-good schedule line through its trailing
    content (readings, cases, due dates for the last entry) until a real
    end-of-section signal fires — a closing-section keyword (grading,
    honor code, ...) or a numbered top-level heading — or the document
    ends. Shared by every strategy that needs to make sure the FINAL
    entry's content doesn't get cut off right at its own marker line."""
    last_line = from_line
    for i in range(from_line + 1, len(lines)):
        text = lines[i].text
        low = text.lower()
        end_hint = any(hint in low and _looks_like_heading(text, hint) for hint in END_SECTION_HINTS)
        if end_hint or _SECTION_HEADING_RE.match(text):
            break
        last_line = i
    return last_line


def _bound_schedule_run(lines: list[Line], search_from: int) -> tuple[Optional[int], Optional[int]]:
    """Find where the schedule section starts and ends.

    `end` must cover the LAST entry's trailing content (its readings/cases/
    articles), not just the last line that itself looks like a date marker
    — otherwise the final session in the schedule silently loses everything
    after its own header line, since nothing after it matches
    `_line_is_schedule_row` to keep `end` advancing. Once inside the
    section, every line extends `end` until a real end-of-section signal
    fires: a known closing-section keyword (grading, honor code, ...) or a
    numbered top-level heading ('4 Assignments and Other Deliverables',
    '4.1 Problem Sets') — the latter catches sections a fixed keyword list
    would miss."""
    start = None
    last_line = None
    for i in range(search_from, len(lines)):
        text = lines[i].text
        if _line_is_schedule_row(text):
            if start is None:
                start = i
            last_line = i
            continue
        if start is None:
            continue
        low = text.lower()
        end_hint = any(hint in low and _looks_like_heading(text, hint) for hint in END_SECTION_HINTS)
        if end_hint or _SECTION_HEADING_RE.match(text):
            break
        last_line = i
    return start, last_line


STRATEGIES = ["header_anchored", "pattern_sweep", "table_extraction"]


def find_markers(pdf_path: str, lines: list[Line], strategy: str) -> tuple[Optional[int], Optional[int]]:
    if strategy == "header_anchored":
        return strategy_header_anchored(lines)
    if strategy == "pattern_sweep":
        return strategy_pattern_sweep(lines)
    raise ValueError(f"unknown line-based strategy {strategy}")


# ---------------------------------------------------------------------------
# Steps 2-4: tuple parsing
# ---------------------------------------------------------------------------

FIELD_HINTS = {
    "reading": re.compile(r"\b(read(ing)?s?|chapter|ch\.|pp?\.\s?\d)", re.IGNORECASE),
    "documents": re.compile(
        r"\b(due|submit|assignment|hw\b|homework|deliverable|upload|documents?\s*:)",
        re.IGNORECASE,
    ),
}

# Explicit case-study marker. Different syllabi label cases differently: some
# use a trailing '(Case)' annotation, others 'Case 1: Southwest Airlines...'
# inline, and others a bare 'CASE:' label with no number at all.
_CASE_STUDY_RE = re.compile(r"\(case\)|\bcase\s*\d*\s*:", re.IGNORECASE)

# Newspaper/magazine citations are recognized by publication name rather than
# by keyword, since the line shape (Author, "Title," Publication, Date) has
# no distinctive verb the way "due"/"submit" or "(Case)" do.
_ARTICLE_PUBLICATIONS = [
    "wall street journal", "new york times", "the new york times", "cnbc",
    "bloomberg", "reuters", "forbes", "financial times", "washington post",
    "los angeles times", "usa today", "the economist", "harvard business review",
    "associated press", "npr",
]
_ARTICLE_RE = re.compile("|".join(re.escape(p) for p in _ARTICLE_PUBLICATIONS), re.IGNORECASE)

_NEW_ITEM_START_RE = re.compile(
    r"^([\u2022\-\*]\s+|read:|reading:|prepare:|case\s*\d*\s*:|guest speaker:|due:|ch\.?\s*\d+)",
    re.IGNORECASE,
)

_TIME_JUNK_RE = re.compile(r"\d{1,2}[.:]\d{2}\s*(am|pm)|\b(am|pm)\b", re.IGNORECASE)


def _is_page_number_artifact(text: str) -> bool:
    """A bare 1-3 digit line is almost always a stray page-footer number
    picked up mid-column, not real content — drop it rather than merging it
    into whichever bullet happens to precede it."""
    return bool(re.fullmatch(r"\d{1,3}", text.strip()))


_LABEL_NOISE_RE = re.compile(
    r"^(readings?|assignments?( due)?|survey due|topics?)$", re.IGNORECASE
)
_LABEL_PREFIX_RE = re.compile(
    r"^(readings?|assignments?( due)?|survey due|topics?)\s*[-:]?\s*", re.IGNORECASE
)


def _is_label_noise(text: str) -> bool:
    """A bare column-header word ('Readings', 'Assignment') with nothing
    else on its own line — some PDF table layouts strand these when a cell
    label and its content end up on different physical rows. Left alone,
    a word like 'Readings' merges onto whatever item precedes it, and if
    that happens to be the real topic sentence, the combined string now
    contains 'Readings' — which then matches the reading-content keyword
    and misclassifies the topic itself as a reading. Drop these outright
    rather than merging them anywhere."""
    return bool(_LABEL_NOISE_RE.match(text.strip()))


def _strip_stray_label_prefix(text: str) -> str:
    """Strip a leading 'Readings -' / 'Assignment:' style label.

    A label immediately followed by ':' or '-' ('Readings:', 'Assignment
    -') is a strong standalone-label signal on its own and gets stripped
    unconditionally. Without that separator ('Readings are posted on
    bCourses...'), the word is far more likely to be the real subject of
    an ordinary sentence, so it's only stripped when doing so reveals a
    recognizable new-item start underneath (a bullet, a case/read/due
    label, a chapter ref) — otherwise stripping would silently mutilate a
    normal sentence rather than remove noise."""
    m = _LABEL_PREFIX_RE.match(text)
    if not m:
        return text
    remainder = text[m.end():]
    has_separator = bool(re.search(r"[-:]", m.group(0)))
    if has_separator or not remainder.strip() or _NEW_ITEM_START_RE.match(remainder):
        return remainder
    return text


def _merge_wrapped_items(content_lines: list[str]) -> list[str]:
    """Reassemble wrapped physical lines back into logical items.

    Used both for narrative bullet lists (wrapped across PDF text lines)
    and for multi-line table cells (wrapped within one cell). Neither case
    can be split on every line break — a table cell wraps a chapter
    citation like 'Ch. 16 (16.1-' / '16.7, 16.9)' across two lines with no
    marker at all, so splitting there would tear the citation in half. A
    new item starts only when a line actually looks like the start of one
    (a bullet, or a recognizable label like 'Case 3:', 'Ch. 17', 'Read:');
    anything else is a continuation of whatever's currently being built."""
    items: list[str] = []
    current: Optional[str] = None
    for raw_text in content_lines:
        if _is_page_number_artifact(raw_text) or _is_label_noise(raw_text):
            continue
        text = _strip_stray_label_prefix(raw_text)
        if not text.strip():
            continue
        if current is None or _NEW_ITEM_START_RE.match(text):
            if current is not None:
                items.append(current)
            current = text
        else:
            current = current + " " + text
    if current is not None:
        items.append(current)
    return items


def _classify_into(t: SessionTuple, item: str) -> None:
    """Route one logical content item (a reassembled bullet, or a table
    cell's line) into the right SessionTuple field, by the same priority
    used everywhere: explicit due/submit language, then an explicit case
    marker, then a recognized publication, then generic reading language,
    then topic (if still empty), then misc."""
    if FIELD_HINTS["documents"].search(item):
        t.documents = (t.documents + " " + item) if t.documents else item
    elif _CASE_STUDY_RE.search(item):
        t.case_study = (t.case_study + " " + item) if t.case_study else item
    elif _ARTICLE_RE.search(item):
        t.article = (t.article + " " + item) if t.article else item
    elif FIELD_HINTS["reading"].search(item):
        t.reading = (t.reading + " " + item) if t.reading else item
    elif t.topic is None:
        t.topic = item
    else:
        t.misc = (t.misc + " " + item) if t.misc else item


def _find_markers(lines: list[Line], start: int, end: int) -> list[tuple[int, int, Optional[str], Optional[str]]]:
    """Locate every marker and its inline topic remainder (if any).

    Returns (marker_start, content_start, date_str, remainder) per marker.
    marker_start is where THIS marker begins — the boundary the PREVIOUS
    tuple's content must stop before. content_start is the last line THIS
    marker itself consumed (its own line, or the following line if a
    date-list got pulled from there) — the boundary THIS tuple's own
    content starts after. They differ exactly when a marker consumes a
    second physical line for its date list; conflating them would let the
    previous tuple's content span swallow this marker's own opening line.

    Two shapes exist across real syllabi. Most anchor on an inline date
    ('1. 8/24: Introduction...', '9/3 - Intro to Ops'), where whatever
    follows the date on the same line genuinely IS the topic. But
    session/block-keyword markers ('BLOCK 2:', 'Week 3') behave
    differently: the real topic always lives on a LATER line, never
    appended after the keyword, and the date/time info can even be split
    onto its own following physical line ('BLOCK 2:' / 'June 5 (...); June
    6 (...)'). Treating a block keyword's trailing text as topic would
    capture stray timing info instead of the real topic; this keeps the
    two shapes separate rather than forcing one heuristic to fit both.
    """
    markers = []
    i = start
    while i <= end:
        text = lines[i].text
        stripped = _LEADING_MARKER_RE.sub("", text, count=1)
        session_match = _session_re.match(stripped)
        if session_match:
            marker_start = i
            inline_remainder_raw = stripped[session_match.end():].strip(" -:\u2013\u2014")
            date_strs = [m.group(0) for m in _date_re.finditer(text)]
            content_start = i
            extra_topic_lines: list[str] = []

            if not date_strs:
                # No date on the marker's own line. Two known shapes here:
                # 'BLOCK 2:' with the date list on the very next line, or
                # 'Class 2 Seed Stage: Founders...' where the TITLE itself
                # wraps across one or more extra lines before the date
                # appears ('Class 2 Seed Stage: Founders, Bootstrapping,
                # Friends/Family Capital,' / 'Crowdfunding, Convertible
                # Debt and SAFEs' / 'September 5th, 2025 8:30-11:30am').
                # Scan a small bounded window ahead for the date, treating
                # any intervening lines as continuation of the topic.
                lookahead_limit = min(end, i + 4)
                j = i + 1
                found_date_line = None
                while j <= lookahead_limit:
                    j_dates = [m.group(0) for m in _date_re.finditer(lines[j].text)]
                    if j_dates:
                        found_date_line = j
                        date_strs = j_dates
                        break
                    extra_topic_lines.append(lines[j].text)
                    j += 1
                if found_date_line is not None:
                    content_start = found_date_line
                    i = found_date_line
                else:
                    extra_topic_lines = []
            elif text.count("(") > text.count(")") and i + 1 <= end:
                # The marker line already has date(s) but ends mid-
                # parenthetical — e.g. 'July 19 (8.30-' wraps to '11.30
                # PST)' on the next physical line. Consume that fragment
                # too so it doesn't get misread as real session content
                # (and, worse, absorb the real topic line into itself).
                content_start = i + 1
                i += 1

            if not date_strs:
                # No date anywhere nearby — this is very likely a wrapped
                # sentence that just happens to START with 'Block 2' or
                # 'Class 3' by coincidence of where the line broke, not a
                # real schedule marker. A genuine marker always has a date
                # attached, either inline, on the next line, or within a
                # short lookahead window past a wrapping title.
                i += 1
                continue

            # Whatever followed the session keyword on its own line MIGHT
            # be the real topic ('Class 1 (1/9) topic Intro, present
            # value...') or might be pure date/time info with no real
            # topic at all ('BLOCK 1: May 16 (4.30-7.30 pm PST); May 17
            # (12.15-3.15 pm PST)'). A bare "does it contain a date"
            # check can't tell these apart — the Class-1 shape has BOTH a
            # date and real topic text on the same line. So strip out the
            # matched date substrings specifically, then check what's left:
            # if it still looks like clock-time debris ('pm PST)', '3.30 -
            # 6.30pm'), there was never a real topic here and the whole
            # thing is discarded; otherwise the leftover text (with a
            # redundant inline 'topic' label word trimmed) is the topic.
            inline_topic = None
            if inline_remainder_raw:
                date_removed = _date_re.sub("", inline_remainder_raw)
                if not _TIME_JUNK_RE.search(date_removed):
                    cleaned = re.sub(r"^[\s()\-:;,]+", "", date_removed)
                    cleaned = re.sub(r"^topics?\s*[-:]?\s*", "", cleaned, flags=re.IGNORECASE)
                    cleaned = cleaned.strip()
                    if len(cleaned) >= 3:
                        inline_topic = cleaned

            remainder_parts = []
            if inline_topic:
                remainder_parts.append(inline_topic)
            remainder_parts.extend(t for t in extra_topic_lines if t.strip())
            remainder = " ".join(remainder_parts) if remainder_parts else None

            date_str = ", ".join(date_strs)
            markers.append((marker_start, content_start, date_str, remainder))
        elif _line_is_schedule_row(text):
            date_match = _date_re.search(text) or _session_re.search(text)
            date_str = date_match.group(0) if date_match else None
            remainder = text[date_match.end():].strip(" -:\u2013\u2014") if date_match else text
            markers.append((i, i, date_str, remainder))
        i += 1
    return markers


def parse_tuples(lines: list[Line], start: int, end: int) -> list[SessionTuple]:
    # Pass 1: locate every marker and its inline topic remainder.
    markers = _find_markers(lines, start, end)
    if not markers:
        return []

    # Pass 2: for each marker, reassemble its content span into logical
    # bullet items (undoing PDF line-wrap) before classifying anything.
    tuples: list[SessionTuple] = []
    for idx, (marker_start, content_start, date_str, remainder) in enumerate(markers):
        # Content runs from just after THIS marker's own last consumed line
        # up to (not including) the NEXT marker's opening line — using the
        # next marker's content_start here would let this tuple swallow
        # the next marker's own text whenever that marker consumed a
        # second line for its date list.
        next_marker_start = markers[idx + 1][0] if idx + 1 < len(markers) else end + 1
        content_indices = list(range(content_start + 1, next_marker_start))
        items = _merge_wrapped_items([lines[i].text for i in content_indices])

        t = SessionTuple(date=date_str, topic=remainder or None,
                          source_lines=[marker_start] + content_indices)
        for item in items:
            _classify_into(t, item)
        tuples.append(t)
    return tuples


def check_pattern_repeats(tuples: list[SessionTuple], min_repeats: int = 2) -> bool:
    hits = sum(1 for t in tuples if t.is_minimally_valid())
    return hits >= min_repeats


# ---------------------------------------------------------------------------
# Step 5: validation, with quarantine instead of all-or-nothing rejection
# ---------------------------------------------------------------------------

def _per_tuple_errors(t: SessionTuple) -> list[str]:
    errs = []
    if not t.date:
        errs.append("missing date/session marker")
    if not t.topic:
        errs.append("missing topic")
    elif len(t.topic) > 200:
        errs.append("topic implausibly long — likely mis-parsed content")
    return errs


def validate_tuples(tuples: list[SessionTuple], min_repeats: int = 2,
                     min_valid_ratio: float = 0.5) -> ValidationReport:
    valid, quarantined = [], []
    for t in tuples:
        errs = _per_tuple_errors(t)
        if errs:
            quarantined.append((t, errs))
        else:
            valid.append(t)

    structural = []
    if not tuples:
        structural.append("no tuples produced")
        return ValidationReport(valid=valid, quarantined=quarantined, structural_failures=structural)

    if not check_pattern_repeats(valid, min_repeats=min_repeats):
        structural.append(
            f"only {len(valid)} valid tuple(s); need at least {min_repeats} "
            f"to trust this as a recurring schedule pattern"
        )

    ratio = len(valid) / len(tuples)
    if ratio < min_valid_ratio:
        structural.append(
            f"only {len(valid)}/{len(tuples)} tuples passed validation "
            f"({ratio:.0%} < required {min_valid_ratio:.0%})"
        )

    numeric_dates = []
    for t in valid:
        if t.date and re.match(r"^\d{1,2}/\d{1,2}", t.date):
            m, d = t.date.split("/")[:2]
            numeric_dates.append((int(m), int(d)))
    if len(numeric_dates) >= 2:
        out_of_order = sum(
            1 for a, b in zip(numeric_dates, numeric_dates[1:])
            if b < a and not (a[0] > 6 and b[0] < 6)   # allow Dec -> Jan wraparound
        )
        if out_of_order > len(numeric_dates) // 2:
            structural.append("dates are not in chronological order")

    return ValidationReport(valid=valid, quarantined=quarantined, structural_failures=structural)


# ---------------------------------------------------------------------------
# Orchestration: steps 1-6 with retry across strategies
# ---------------------------------------------------------------------------

def _tuple_richness(t: SessionTuple) -> int:
    """Count populated fields beyond date/topic, as a tie-breaker between
    candidate results with the same tuple count — prefers whichever
    extraction actually pulled out reading/case/article/documents detail
    over one that only got bare date+topic."""
    return sum(1 for f in (t.reading, t.case_study, t.article, t.documents, t.misc) if f)


def run_pipeline(pdf_path: str, max_attempts: int = 3) -> ParseResult:
    """Try every strategy — don't stop at the first one that validates.

    Strategies aren't interchangeable fallbacks that only differ in whether
    they succeed; they can all "succeed" (pass structural validation) while
    differing hugely in quality. A syllabus's front-matter meeting-time
    listing ('Sunday, Feb 8th: 3:30-6:30pm') can look exactly like a valid
    3-entry schedule to pattern_sweep, while the real 5-session detail table
    sits untried in table_extraction — stopping at the first pass would
    silently return the worse answer. Instead, every strategy that
    validates becomes a candidate, and the richest one wins: most valid
    tuples first, most populated fields as a tie-breaker.
    """
    lines = extract_lines(pdf_path)
    all_failures = []
    candidates = []  # (strategy, attempt, start, end, report)

    for attempt in range(1, max_attempts + 1):
        strategy = STRATEGIES[(attempt - 1) % len(STRATEGIES)]

        if strategy == "table_extraction":
            tuples = extract_table_schedule(pdf_path)
            start = end = None
            if not tuples:
                all_failures.append(
                    {"attempt": attempt, "strategy": strategy,
                     "reason": "no schedule-shaped table found"}
                )
                continue
        else:
            start, end = find_markers(pdf_path, lines, strategy)
            if start is None or end is None:
                all_failures.append(
                    {"attempt": attempt, "strategy": strategy,
                     "reason": "no start/end schedule markers found"}
                )
                continue
            tuples = parse_tuples(lines, start, end)

        report = validate_tuples(tuples)
        if report.ok:
            candidates.append((strategy, attempt, start, end, report))
        else:
            all_failures.append(
                {"attempt": attempt, "strategy": strategy, "reason": report.structural_failures}
            )

    if candidates:
        # Structural confidence, not raw tuple count, decides the winner.
        # table_extraction reads real PDF table columns — the strongest
        # possible grounding. header_anchored is windowed against an
        # actual document heading, so it stays scoped to the real
        # schedule section. pattern_sweep has no structural anchor at
        # all — it's pure date-clustering — so it can sweep in adjacent
        # but out-of-scope content (e.g. an 'Important Dates' bullet list
        # right before the real class schedule) that each individually
        # passes per-tuple validation, inflating its tuple count without
        # actually being more correct. Preferring raw count would pick
        # that noisier result purely because it's bigger.
        strategy_priority = {"table_extraction": 0, "header_anchored": 1, "pattern_sweep": 2}

        def score(c):
            strat, _, _, _, report = c
            return (
                -strategy_priority.get(strat, 99),
                len(report.valid),
                sum(_tuple_richness(t) for t in report.valid),
            )
        strategy, attempt, start, end, report = max(candidates, key=score)
        return ParseResult(
            success=True,
            strategy=strategy,
            attempts=attempt,
            start_marker=start,
            end_marker=end,
            tuples=[asdict(t) for t in report.valid],
            quarantined=[
                {"tuple": asdict(t), "reasons": reasons}
                for t, reasons in report.quarantined
            ],
            failures=[],
            message=(
                f"Parsed {len(report.valid)} valid session tuple(s) "
                f"(+{len(report.quarantined)} quarantined) using "
                f"'{strategy}' strategy (best of {len(candidates)} candidate(s) "
                f"across {max_attempts} attempts)."
            ),
        )

    return ParseResult(
        success=False,
        strategy=STRATEGIES[(max_attempts - 1) % len(STRATEGIES)],
        attempts=max_attempts,
        start_marker=None,
        end_marker=None,
        tuples=[],
        quarantined=[],
        failures=all_failures,
        message="Failed to Parse syllabus document",
    )


def run_and_report(pdf_path: str) -> str:
    result = run_pipeline(pdf_path)
    return json.dumps(asdict(result), indent=2)


# ---------------------------------------------------------------------------
# Backend adapter: PDF bytes -> the session shape the syllabus processor uses
# ---------------------------------------------------------------------------

def map_pdf_bytes_to_sessions(pdf_bytes: bytes) -> list[dict]:
    """Parse syllabus PDF bytes and adapt the result to the shape the syllabus
    processor consumes: [{index, week, title, date, topics}].

    Returns [] when nothing parses, so the caller can fall back to the
    text-based parser. Each tuple's topic becomes the session title and is split
    into concept labels for in_scope_concepts (same splitter the text parser
    uses); the raw date string is passed through for the caller to normalize.
    """
    import os
    import tempfile
    from backend.app.syllabus_parser import _split_header_topics

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name
        result = run_pipeline(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    if not result.success or not result.tuples:
        return []

    sessions: list[dict] = []
    for i, t in enumerate(result.tuples):
        topic = (t.get("topic") or "").strip()
        if not topic:
            continue
        topics = _split_header_topics(topic) or [topic]
        sessions.append({
            "index": i + 1,
            "week": "Session %d" % (i + 1),
            "title": topic,
            "date": (t.get("date") or "").strip(),
            "topics": topics,
        })
    return sessions


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/user-data/uploads/Syllabus_GSI_Fall24.pdf"
    print(run_and_report(path))
