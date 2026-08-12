"""End-to-end smoke test against a deployed environment.

Exercises the flows a user actually walks, so a schema or handler change that
breaks one of them fails here instead of in front of a professor. Written after
a migration silently broke multi-turn answering: each fix had been verified in
isolation, but nobody re-ran the flow the changed table participates in.

Usage:
    python -m tests.smoke_flows                     # against the deployed URL
    python -m tests.smoke_flows --base http://...   # against something else

Exit code 0 = all flows passed, 1 = at least one failed.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://d3fxwe1wjfkcz0.cloudfront.net"

# Populated by authenticate() at startup: the server derives identity from the
# token, so raw x-role headers no longer grant anything.
PROF: dict = {}
STUDENT: dict = {}

CREDENTIALS = {
    "professor": ("prof1@univ.edu", "epistemy123"),
    "student": ("student7@univ.edu", "student123"),
}

# Exam questions are AI-generated per course, so no fixed answer text can be
# on-topic. These only exercise the request path; they say nothing about whether
# scoring is correct, and an early version of this file wrongly blamed the scorer
# for the zeros they produce. Grading accuracy needs graded fixtures instead —
# see eds_representative_responses.md.
GENERIC_ANSWERS = [
    "I think this relates to the core idea in the material.",
    "It matters because the first factor influences the second, which then "
    "changes the outcome the question is asking about.",
    "The mechanism runs in a chain: the initial condition drives an intermediate "
    "effect, that effect constrains the next step, and the constraint is what "
    "produces the result. A common misreading is to treat the steps as "
    "independent when the middle link is what carries the causality.",
]

_failures: list[str] = []
_warnings: list[str] = []


def call(base, method, path, headers, body=None, timeout=120):
    """Return (status, parsed_body). Never raises on HTTP error status."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = dict(headers)
    hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read() or b"{}"
        return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:300].decode(errors="replace")
    except Exception as exc:  # network/timeout
        return 0, str(exc)


def check(label: str, ok: bool, detail: str = "") -> bool:
    """Record and print a single assertion."""
    suffix = f"  — {detail}" if (detail and not ok) else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if not ok:
        _failures.append(label)
    return ok


def warn(label: str, detail: str = "") -> None:
    """Flag something suspicious that is not a hard failure."""
    print(f"  WARN  {label}" + (f"  — {detail}" if detail else ""))
    _warnings.append(label)


def authenticate(base) -> bool:
    """Log both roles in and cache their bearer headers. False if login is broken."""
    print("\n[auth]")
    ok = True
    for role, (email, password) in CREDENTIALS.items():
        st, body = call(base, "POST", "/api/auth/login", {},
                        {"email": email, "password": password})
        token = body.get("token") if isinstance(body, dict) else None
        if not check(f"{role} login returns a token", st == 200 and bool(token),
                     f"HTTP {st}: {body}"):
            ok = False
            continue
        target = PROF if role == "professor" else STUDENT
        target.clear()
        target["Authorization"] = f"Bearer {token}"

    # The whole point of this work: a self-declared role must no longer be accepted.
    st, _ = call(base, "GET", "/api/professor/dashboard",
                 {"x-org-name": "epistemy", "x-user-id": "student3@univ.edu",
                  "x-role": "professor"})
    check("spoofed role header is rejected", st == 401, f"got {st}, expected 401")

    st, _ = call(base, "GET", "/api/professor/dashboard",
                 {"Authorization": "Bearer not-a-real-token"})
    check("forged token is rejected", st == 401, f"got {st}, expected 401")
    return ok


def flow_health(base) -> None:
    print("\n[health]")
    st, body = call(base, "GET", "/health", PROF)
    check("GET /health is 200", st == 200, f"got {st}")


def flow_professor(base) -> str | None:
    """Read-only professor surface. Returns a course_id to reuse, if any."""
    print("\n[professor]")
    st, dash = call(base, "GET", "/api/professor/dashboard", PROF)
    if not check("dashboard is 200", st == 200, f"got {st}"):
        return None

    courses = dash.get("courses") or []
    check("dashboard returns courses", bool(courses), f"{len(courses)} found")
    if not courses:
        return None

    course_id = courses[0]["course_id"]
    for label, path in (
        ("course detail", f"/api/courses/{course_id}"),
        ("concept graph", f"/api/courses/{course_id}/graph"),
        ("questions", f"/api/courses/{course_id}/questions"),
        ("assignments", f"/api/courses/{course_id}/assignments"),
    ):
        st, _ = call(base, "GET", path, PROF)
        check(f"{label} is 200", st == 200, f"got {st}")
    return course_id


def flow_student_exam(base) -> None:
    """The full exam: start, three turns on one question, advance, submit."""
    print("\n[student exam]")
    st, dash = call(base, "GET", "/api/student/dashboard", STUDENT)
    if not check("student dashboard is 200", st == 200, f"got {st}"):
        return

    assignments = dash.get("active_assignments") or dash.get("assignments") or []
    if not assignments:
        warn("no active assignment to exercise", "create one to cover this flow")
        return

    aid = assignments[0].get("assignment_id") or assignments[0].get("id")
    st, sess = call(base, "POST", f"/api/assignments/{aid}/start", STUDENT, {})
    if not check("start exam is 200", st == 200, f"got {st}: {sess}"):
        return

    sid = sess.get("session_id")
    questions = sess.get("questions") or []
    if not check("session has questions", bool(sid) and bool(questions),
                 f"{len(questions)} questions"):
        return

    # Multi-turn is the regression that motivated this file: turn 1 used to pass
    # and turn 2 crashed on a stale ON CONFLICT target.
    saw_components = False
    for i, answer in enumerate(GENERIC_ANSWERS, start=1):
        st, r = call(base, "POST", f"/api/sessions/{sid}/answer", STUDENT,
                     {"question_index": 0, "answer_text": answer})
        if not check(f"turn {i} is 200", st == 200, f"got {st}: {r}"):
            return
        if i < len(GENERIC_ANSWERS):
            check(f"turn {i} returns a probe", bool(r.get("probe")), "empty probe")
        # Assert the scorer RAN (components present), not what it returned — these
        # answers are deliberately generic, so a low score is the correct output.
        if isinstance(r.get("eds_components"), dict):
            saw_components = True

    check("EDS scorer ran on the answers", saw_components,
          "no eds_components in any response — question may lack expected_path")

    if len(questions) > 1:
        st, _ = call(base, "POST", f"/api/sessions/{sid}/answer", STUDENT,
                     {"question_index": 1, "answer_text": GENERIC_ANSWERS[-1]})
        check("second question accepts an answer", st == 200, f"got {st}")

    st, status = call(base, "GET", f"/api/sessions/{sid}/status", STUDENT)
    check("session status is 200", st == 200, f"got {st}")
    if st == 200:
        check("all turns persisted", len(status.get("turns") or []) >= len(GENERIC_ANSWERS),
              f"{len(status.get('turns') or [])} turns stored")

    st, done = call(base, "POST", f"/api/sessions/{sid}/complete", STUDENT, {})
    check("submit exam is 200", st == 200, f"got {st}: {done}")


def flow_grades(base) -> None:
    print("\n[grades]")
    st, dash = call(base, "GET", "/api/professor/dashboard", PROF)
    if st != 200 or not (dash.get("courses") or []):
        warn("skipped", "no courses")
        return
    for course in dash["courses"]:
        st, assigns = call(base, "GET",
                           f"/api/courses/{course['course_id']}/assignments", PROF)
        if st == 200 and assigns:
            aid = assigns[0]["assignment_id"]
            st, _ = call(base, "GET", f"/api/assignments/{aid}/sessions", PROF)
            check("assignment sessions is 200", st == 200, f"got {st}")
            return
    warn("no assignment found to check grades", "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    args = parser.parse_args()

    print(f"smoke flows against {args.base}")
    if not authenticate(args.base):
        print("\nlogin failed — remaining flows would all 401, stopping here")
        return 1
    flow_health(args.base)
    flow_professor(args.base)
    flow_student_exam(args.base)
    flow_grades(args.base)

    print(f"\n{'-' * 60}")
    if _failures:
        print(f"FAILED  {len(_failures)} check(s):")
        for f in _failures:
            print(f"  - {f}")
    else:
        print("all checks passed")
    if _warnings:
        print(f"{len(_warnings)} warning(s):")
        for w in _warnings:
            print(f"  - {w}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
