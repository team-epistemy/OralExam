"""Syllabus -> sessions + topics parsing (the ingestion engine, server-side)."""
from backend.app.syllabus_parser import parse_syllabus, normalize_date


def test_inline_dated_with_colon_and_dash():
    text = (
        "Operations Management — MBA 611\n\n"
        "Week 1 (Sep 2): Course intro; process fundamentals; the operations strategy triangle\n"
        "Week 2 (Sep 9): Process analysis — flow rate, bottlenecks, Little's Law\n"
    )
    s = parse_syllabus(text)
    assert len(s) == 2
    assert s[0]["week"] == "Week 1"
    assert s[0]["date"] == "Sep 2"
    assert "Course intro" in s[0]["topics"]
    assert "The operations strategy triangle" in s[0]["topics"]
    # "— topics" splits the title from the topic list
    assert s[1]["title"] == "Process analysis"
    assert s[1]["topics"] == ["Flow rate", "Bottlenecks", "Little's Law"]


def test_bulleted_multiline_classes():
    text = (
        "CS 189\n\n"
        "Class 1 — September 3\n- The learning problem\n- Supervised vs. unsupervised learning\n\n"
        "Class 2 — September 10\n- Linear regression\n- Gradient descent\n"
    )
    s = parse_syllabus(text)
    assert len(s) == 2
    assert s[0]["week"] == "Class 1"
    assert s[0]["date"] == "September 3"
    assert s[0]["topics"] == ["The learning problem", "Supervised vs. unsupervised learning"]
    assert s[1]["topics"] == ["Linear regression", "Gradient descent"]


def test_session_numbering_with_slash_date():
    text = "Session 1. 1/13: Time value of money, present value, discounting\n"
    s = parse_syllabus(text)
    assert len(s) == 1
    assert s[0]["week"] == "Session 1"
    assert s[0]["date"] == "1/13"
    assert s[0]["topics"] == ["Time value of money", "Present value", "Discounting"]


def test_no_schedule_returns_empty():
    assert parse_syllabus("Just a paragraph about the course with no weekly schedule.") == []
    assert parse_syllabus("") == []


def test_topics_deduped_and_cleaned():
    s = parse_syllabus("Week 1: intro; Intro; the")  # dupes + stopword dropped
    assert s[0]["topics"] == ["Intro"]


def test_comma_inside_parens_not_split():
    s = parse_syllabus("Week 1: regression (linear, logistic); trees")
    assert s[0]["topics"] == ["Regression (linear, logistic)", "Trees"]


def test_normalize_date():
    assert normalize_date("2026-09-03", 2026) == "2026-09-03"
    assert normalize_date("Sep 2", 2026) == "2026-09-02"
    assert normalize_date("September 10", 2026) == "2026-09-10"
    assert normalize_date("1/13", 2026) == "2026-01-13"
    assert normalize_date("1/13/2027", 2026) == "2027-01-13"
    assert normalize_date("3 Sept", 2026) == "2026-09-03"
    assert normalize_date("", 2026) is None
    assert normalize_date("sometime", 2026) is None
