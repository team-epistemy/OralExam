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
    # A dash introduces a sub-list: title + items all become topics.
    assert s[1]["topics"] == ["Process analysis", "Flow rate", "Bottlenecks", "Little's Law"]


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


def test_numbered_classes_with_reading_bullets_and_modules():
    """Real-world format: 'N. M/D: Title' classes, Roman-numeral module headers,
    and reading citations as bullets (which must NOT become topics)."""
    text = "\n".join([
        "Module I: Decision Analysis",
        "1. 8/24: Introduction and the Basics of Decision Making",
        "• Lecture notes for 201a, Hermalin (H) pp. 1-8",
        "• Samuelson & Marks (SM) [7th: Ch.1, Ch.7 to p. 295]",
        "2. 8/25: Decision Making under Uncertainty: The Value of Information and Options",
        "• H pp. 9-26",
        "Module II: Production and Costs",
        "3. 8/29: Introduction to Economic Costs",
        "• H pp. 29-44",
        "3",                                   # stray page number — must be ignored
        "13. 10/5: Sequential Games, Vertical Relationships, Firm Boundaries and Contracting",
        "• SM Ch.14",
    ])
    s = parse_syllabus(text)
    assert len(s) == 4                          # 4 numbered classes, modules/page-num ignored
    assert s[0]["week"] == "Session 1" and s[0]["date"] == "8/24"
    assert s[0]["topics"] == ["Introduction and the Basics of Decision Making"]
    # reading bullets are not topics
    assert not any("Hermalin" in t or "Samuelson" in t for t in s[0]["topics"])
    # internal ':' is kept (single descriptive topic)
    assert s[1]["topics"] == ["Decision Making under Uncertainty: The Value of Information and Options"]
    # comma-separated title -> multiple topics
    assert s[3]["week"] == "Session 13"
    assert s[3]["topics"] == ["Sequential Games", "Vertical Relationships", "Firm Boundaries and Contracting"]


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
