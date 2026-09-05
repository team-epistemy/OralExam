"""Tests for the PDF syllabus mapper's backend adapter and core parsing.

The heavy pdfplumber path is monkeypatched, so the tuple→session adapter and the
line-level schedule detection are exercised without a real PDF or the PDF stack.
"""
from backend.app import syllabus_mapper as M


def test_module_imports_without_pdfplumber():
    # pdfplumber is imported lazily; the module + adapter must import cleanly.
    assert hasattr(M, "map_pdf_bytes_to_sessions")
    assert hasattr(M, "run_pipeline")


def test_line_is_schedule_row_anchors_at_start():
    # Real schedule markers.
    assert M._line_is_schedule_row("1. 8/24: Introduction to Operations")
    assert M._line_is_schedule_row("Week 3 Process Analysis")
    assert M._line_is_schedule_row("9/3 - Intro to Ops")
    # Citation dates buried in a reading bullet must NOT count as rows.
    assert not M._line_is_schedule_row("Smith, \"Pricing,\" Wall Street Journal, 7/21/2023")
    # A bare stranded date with no topic text must NOT count.
    assert not M._line_is_schedule_row("11/10/2022")


def _fake_result(tuples, success=True):
    return M.ParseResult(
        success=success, strategy="table_extraction", attempts=1,
        start_marker=0, end_marker=9, tuples=tuples, quarantined=[],
        failures=[], message="ok")


def test_map_pdf_bytes_to_sessions_adapts_tuples(monkeypatch):
    tuples = [
        {"date": "8/24", "topic": "Introduction to Operations", "reading": "Ch. 1"},
        {"date": "9/3", "topic": "Process analysis — flow rate, bottlenecks"},
    ]
    monkeypatch.setattr(M, "run_pipeline", lambda path, **kw: _fake_result(tuples))

    sessions = M.map_pdf_bytes_to_sessions(b"%PDF-fake")
    assert len(sessions) == 2

    s0 = sessions[0]
    assert s0["index"] == 1 and s0["week"] == "Session 1"
    assert s0["title"] == "Introduction to Operations"
    assert s0["date"] == "8/24"
    assert s0["topics"] == ["Introduction to Operations"]

    # A dash-delimited topic splits into multiple concept labels for scope.
    assert sessions[1]["topics"] == ["Process analysis", "Flow rate", "Bottlenecks"]


def test_map_pdf_bytes_to_sessions_empty_on_failure(monkeypatch):
    monkeypatch.setattr(M, "run_pipeline", lambda path, **kw: _fake_result([], success=False))
    assert M.map_pdf_bytes_to_sessions(b"%PDF") == []


def test_map_pdf_bytes_to_sessions_skips_topicless_tuples(monkeypatch):
    tuples = [{"date": "8/24", "topic": ""}, {"date": "9/1", "topic": "Forecasting"}]
    monkeypatch.setattr(M, "run_pipeline", lambda path, **kw: _fake_result(tuples))
    sessions = M.map_pdf_bytes_to_sessions(b"%PDF")
    assert [s["title"] for s in sessions] == ["Forecasting"]


def test_adapter_uses_session_number_and_cleans_rich_date(monkeypatch):
    # New mapper: tuples carry session_number and a date that may include a time
    # range for disambiguation. The adapter should key on session_number and hand
    # the caller just the leading calendar date.
    tuples = [{"session_number": 5, "date": "November 14th, 2025 8:30-11:30am",
               "topic": "Capital Structure"}]
    monkeypatch.setattr(M, "run_pipeline", lambda path, **kw: _fake_result(tuples))
    s = M.map_pdf_bytes_to_sessions(b"%PDF")[0]
    assert s["index"] == 5 and s["week"] == "Session 5"
    assert s["date"] == "November 14th"          # time range stripped for normalizing
    assert s["title"] == "Capital Structure"


def test_merge_same_date_tuples_collapses_and_numbers():
    tuples = [
        M.SessionTuple(date="9/3", topic="Part I"),
        M.SessionTuple(date="9/3", topic="Part II"),
        M.SessionTuple(date="9/10", topic="Forecasting"),
    ]
    merged = M.merge_same_date_tuples(tuples)
    assert len(merged) == 2
    assert merged[0].topic == "Part I / Part II"    # same-date topics combined
    M.assign_session_numbers(merged)
    assert [t.session_number for t in merged] == [1, 2]


def test_undated_tuples_never_merge():
    tuples = [M.SessionTuple(date=None, topic="A"), M.SessionTuple(date=None, topic="B")]
    assert len(M.merge_same_date_tuples(tuples)) == 2
