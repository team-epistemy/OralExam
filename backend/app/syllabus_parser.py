"""Parse a course syllabus's class schedule into sessions with mapped topics.

Web-free, stdlib-only (like emails.py / exam_questions.py) so it stays
unit-testable without FastAPI or a DB. Mirrors the client-side mock-up: detect a
per-week / per-class header, pull an optional date, and split the remainder into
topic labels. The topic labels become each session's in_scope_concepts — the
exam builder matches concept nodes by label, so no id resolution is needed here.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

_MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
           r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_DATE_RE = re.compile(
    r"(?:" + _MONTHS + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?"
    r"|\d{1,2}\s+(?:" + _MONTHS + r")"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?", re.I)
_HEADER_RE = re.compile(
    r"^\s*(?:#+\s*|\*+\s*)?(week|class|session|lecture|day)\s*#?\s*(\d+)\b\s*(.*)$",
    re.I)
# A bare-numbered class line ("1. 8/24: Title", "2) Topic") at low indentation —
# the common schedule format that carries no Week/Class keyword. Requires a
# period/paren after the number + real content, so page numbers and wrapped
# lines ("3", "262-267...") don't match. (Modules use Roman numerals and are
# intentionally not treated as classes.)
_NUM_HEADER_RE = re.compile(r"^\s{0,3}(\d{1,2})[.)]\s+(\S.*)$")
_STOP = {"and", "or", "the", "a", "an", "etc", "tbd", "n/a", "readings", "reading"}
_MONTH_NUM = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
              "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _clean_topic(t: str) -> str:
    t = re.sub(r"^[\s\-–—•·▪◦*o]+", "", t)  # leading bullets
    t = re.sub(r"^\s*\d+[.)]\s*", "", t)                                   # "1." numbering
    t = re.sub(r"^\s*[a-z][.)]\s+", "", t, flags=re.I)                     # "a)" numbering
    t = re.sub(r"[\s.;:,]+$", "", t)                                       # trailing punctuation
    t = re.sub(r"^[\"'“”]|[\"'“”]$", "", t)            # wrapping quotes
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 3 or t.lower() in _STOP:
        return ""
    return _cap(t)


def _split_topics(s: str) -> List[str]:
    if not s:
        return []
    norm = s
    norm = re.sub(r"^[ \t]*[-–—•·▪◦*]\s+", "\n", norm, flags=re.M)
    norm = re.sub(r"^[ \t]*\d+[.)]\s+", "\n", norm, flags=re.M)
    norm = re.sub(r"^[ \t]*[a-z][.)]\s+", "\n", norm, flags=re.M | re.I)
    parts = re.split(r"[;\n]|,(?![^(]*\))", norm)  # ; newline, and commas outside parens
    out, seen = [], set()
    for p in parts:
        c = _clean_topic(p)
        if not c:
            continue
        k = c.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _split_header_topics(s: str) -> List[str]:
    """Split a class title line into topics. Dashes introduce a sub-list
    ("Process analysis — flow rate, bottlenecks"), so treat ` — `/` - ` like a
    separator; then split on ; and commas (outside parens). Internal ':' and
    'and'/'&' are kept, so "Decision Making under Uncertainty: The Value..." and
    "Firm Boundaries and Contracting" each stay a single topic."""
    s = re.sub(r"\s+[—–]\s+", "; ", s)
    s = re.sub(r"\s-\s", "; ", s)
    return _split_topics(s)


def normalize_date(s: str, default_year: int) -> Optional[str]:
    """Best-effort 'YYYY-MM-DD' from a matched date string, else None.

    A bare 'Sep 2' with no year uses default_year (the caller passes the current
    year). Anything it can't confidently resolve returns None (session stays
    undated rather than mis-dated)."""
    if not s:
        return None
    s = s.strip()

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    m = re.match(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$", s)
    if m:
        yy = m.group(3)
        year = default_year if not yy else (int(yy) if len(yy) == 4 else 2000 + int(yy))
        return _mk(year, int(m.group(1)), int(m.group(2)))

    m = re.match(r"^(" + _MONTHS + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?$", s, re.I)
    if m:
        mon = _MONTH_NUM.get(m.group(1).lower()[:3])
        year = int(m.group(3)) if m.group(3) else default_year
        return _mk(year, mon, int(m.group(2)))

    m = re.match(r"^(\d{1,2})\s+(" + _MONTHS + r")$", s, re.I)
    if m:
        mon = _MONTH_NUM.get(m.group(2).lower()[:3])
        return _mk(default_year, mon, int(m.group(1)))

    return None


def _mk(year: int, month: Optional[int], day: int) -> Optional[str]:
    if not month or not (1 <= month <= 12) or not (1 <= day <= 31):
        return None
    return "%04d-%02d-%02d" % (year, month, day)


def parse_syllabus(raw: str) -> List[Dict]:
    """Split a syllabus into sessions: [{index, week, title, date, topics}]."""
    text = (raw or "").replace("\r\n", "\n")
    blocks: List[Dict] = []
    cur: Optional[Dict] = None
    for line in text.split("\n"):
        km = _HEADER_RE.match(line)
        nm = None if km else _NUM_HEADER_RE.match(line)
        if km or nm:
            if cur:
                blocks.append(cur)
            if km:
                cur = {"kind": km.group(1).lower(), "num": int(km.group(2)),
                       "rest": km.group(3) or "", "body": []}
            else:
                cur = {"kind": "session", "num": int(nm.group(1)),
                       "rest": nm.group(2) or "", "body": []}
        elif cur is not None:
            cur["body"].append(line)
    if cur:
        blocks.append(cur)

    sessions: List[Dict] = []
    for i, b in enumerate(blocks):
        body_text = "\n".join(b["body"])
        # Prefer a date on the header line; fall back to the body.
        dm = _DATE_RE.search(b["rest"]) or _DATE_RE.search(body_text)
        date = re.sub(r"\s+", " ", dm.group(0)).strip() if dm else ""

        # The class title/topics are on the header line. Drop the date and any
        # leading numbering/date punctuation.
        rest = _DATE_RE.sub("", b["rest"], count=1)
        rest = re.sub(r"^[\s\-–—:().,]+", "", rest)
        rest = re.sub(r"[\s\-–—:().,]+$", "", rest).strip()

        # Topics come from the header title. Bullets under a titled class are
        # readings/citations, so they're ignored — but when the header has no
        # title (e.g. "Class 1 — Sep 3"), the bullets ARE the topics.
        if rest:
            topics = _split_header_topics(rest)
        else:
            topics = _split_topics(body_text)

        seen, deduped = set(), []
        for t in topics:
            k = t.lower()
            if k in seen:
                continue
            seen.add(k)
            deduped.append(t)

        week_label = "%s %d" % (_cap(b["kind"]), b["num"])
        sessions.append({
            "index": i + 1,
            "week": week_label,
            "title": rest or week_label,
            "date": date,
            "topics": deduped,
        })
    return sessions
