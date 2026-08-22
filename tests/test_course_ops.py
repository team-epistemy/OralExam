"""Unit tests for course-ops helpers (email roster parsing)."""
from backend.app.emails import parse_emails as _parse_emails


def test_parse_single_email():
    assert _parse_emails(["Student1@Univ.edu"]) == ["student1@univ.edu"]


def test_parse_comma_joined_string():
    # A pasted CSV that arrives as one array element must still split.
    assert _parse_emails(["a@x.edu, b@x.edu; c@x.edu"]) == ["a@x.edu", "b@x.edu", "c@x.edu"]


def test_parse_dedups_and_drops_invalid():
    got = _parse_emails(["a@x.edu", "a@x.edu", "not-an-email", "", None])
    assert got == ["a@x.edu"]


def test_parse_mixed_list_and_whitespace():
    got = _parse_emails(["  s1@u.edu ", "s2@u.edu\ns3@u.edu"])
    assert got == ["s1@u.edu", "s2@u.edu", "s3@u.edu"]


def test_parse_empty():
    assert _parse_emails([]) == []
    assert _parse_emails(None) == []
