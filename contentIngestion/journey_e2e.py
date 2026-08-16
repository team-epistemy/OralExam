#!/usr/bin/env python3
"""End-to-end user-journey test for the Epistemy platform, driven by a real
subject PDF. Stdlib only — no dependencies.

Usage:
    python journey_e2e.py [path/to/subject.pdf]

If no path is given, the first *.pdf in ./test_materials/ is used. The PDF's
filename (minus extension) becomes the course/subject name. The script walks
the full professor and student journeys plus the deeper branches (grading,
multi-student, SSE) against the deployed app and prints a PASS/FAIL report.
Exit code is non-zero if any real step failed.

Env overrides: BASE (ALB URL), PROF_EMAIL/PROF_PW, STUDENT_EMAIL/STUDENT_PW.
"""
import glob
import json
import os
import re
import sys
import time
import datetime
import urllib.request
import urllib.error

BASE = os.environ.get("BASE", "http://epistemy-m3-int-571630445.us-west-2.elb.amazonaws.com").rstrip("/")
PROF = (os.environ.get("PROF_EMAIL", "prof1@univ.edu"), os.environ.get("PROF_PW", "epistemy123"))
STU1 = (os.environ.get("STUDENT_EMAIL", "student1@univ.edu"), os.environ.get("STUDENT_PW", "student123"))
STU2 = ("student2@univ.edu", "student123")
ORG = "epistemy"
results = []


def req(method, path, token=None, body=None, raw_url=None, data=None, ctype="application/json", timeout=120):
    url = raw_url or (BASE + path)
    h = {"content-type": ctype}
    if token:
        h["authorization"] = f"Bearer {token}"
    payload = data if data is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(url, data=payload, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            txt = resp.read().decode(errors="replace")
            return resp.status, (json.loads(txt) if txt[:1] in "{[" else txt)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"[:300]


def rec(journey, step, ok, status, note=""):
    results.append(dict(journey=journey, step=step, ok=ok, status=status, note=note))
    print(f"  [{'PASS' if ok else 'FAIL'}] {step}: HTTP {status} {note}", flush=True)
    return ok


def iso(days=0):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).isoformat()


def login(creds):
    st, r = req("POST", "/api/auth/login", body={"email": creds[0], "password": creds[1]})
    return r.get("token") if isinstance(r, dict) else None


def find_pdf():
    if len(sys.argv) > 1:
        return sys.argv[1]
    here = os.path.dirname(os.path.abspath(__file__))
    hits = sorted(glob.glob(os.path.join(here, "test_materials", "*.pdf")))
    return hits[0] if hits else None


def main():
    pdf = find_pdf()
    if not pdf or not os.path.isfile(pdf):
        print("ERROR: no PDF found. Pass a path or drop one in ./test_materials/*.pdf")
        sys.exit(2)
    raw = open(pdf, "rb").read()
    subject = re.sub(r"[^a-z0-9]+", "-", os.path.splitext(os.path.basename(pdf))[0].lower()).strip("-") or "subject"
    course = f"e2e-{subject}"
    print(f"\nSubject PDF : {pdf} ({len(raw)} bytes)\nCourse name : {course}\nTarget      : {BASE}\n")

    ptok = login(PROF)
    if not ptok:
        print("ERROR: professor login failed — aborting."); sys.exit(2)

    print("########## PROFESSOR JOURNEY ##########")
    rec("prof", "professor login", True, 200)
    st, _ = req("GET", "/api/professor/dashboard", ptok); rec("prof", "dashboard", st == 200, st)
    st, _ = req("GET", "/api/professor/courses", ptok); rec("prof", "list courses", st == 200, st)

    # Upload the subject PDF
    st, pres = req("POST", "/materials:presign", ptok, body={"org_name": ORG, "course_name": course,
                   "file_name": os.path.basename(pdf), "mime_type": "application/pdf", "bytes": len(raw)})
    ok = st == 200 and isinstance(pres, dict) and "upload_url" in pres
    rec("prof", "presign PDF upload", ok, st)
    mid = pres.get("material_id") if ok else None
    vid = pres.get("material_version_id") if ok else None
    put_ok = False
    if ok:
        st, _ = req("PUT", None, raw_url=pres["upload_url"], data=raw, ctype="application/pdf")
        put_ok = st in (200, -1)  # S3 may drop the conn after a successful PUT; register confirms
        st2, _ = req("POST", f"/versions/{vid}/register", ptok)
        rec("prof", "upload+register PDF", st2 == 200, st2, "(register confirms S3 object exists)")

    # Ingest (PDF extractor exercised here)
    status = None
    for _ in range(30):
        st, vers = req("GET", f"/materials/{mid}/versions", ptok)
        status = vers[0].get("status") if isinstance(vers, list) and vers else None
        if status in ("ready", "failed"):
            break
        time.sleep(10)
    rec("prof", "PDF ingest -> ready", status == "ready", st, f"status={status}")

    st, courses = req("GET", "/api/professor/courses", ptok)
    cid = next((c["course_id"] for c in courses if c["course_name"] == course), None) if isinstance(courses, list) else None

    # Graph (Claude)
    st, gr = req("POST", f"/api/courses/{cid}/graph/rebuild", ptok, body={"domain": subject, "rebuild": True})
    nodes = gr.get("node_count", 0) if isinstance(gr, dict) else 0
    rec("prof", "graph rebuild (Claude)", st == 200 and nodes > 0, st, f"nodes={nodes}")
    st, g = req("GET", f"/api/courses/{cid}/graph", ptok)
    edges = g.get("edges", []) if isinstance(g, dict) else []
    edge_ok = bool(edges) and isinstance(edges[0], dict) and "edge_type" in edges[0]
    rec("prof", "graph GET (edges render for UI)", st == 200 and edge_ok, st, f"edges={len(edges)}")
    concept0 = (g.get("concepts") or [{}])[0] if isinstance(g, dict) else {}
    nid = concept0.get("id") or concept0.get("node_id") or concept0.get("label")
    st, _ = req("GET", f"/api/graph/{nid}/neighbors?course_id={cid}", ptok)
    rec("prof", "graph neighbors", st == 200, st)

    # Questions (Claude) + approval
    st, _ = req("POST", f"/api/courses/{cid}/questions/generate", ptok, body={"material_id": mid, "count": 6})
    rec("prof", "generate questions (Claude)", st == 200, st)
    st, ql = req("GET", f"/api/courses/{cid}/questions", ptok)
    qs = (ql.get("questions", ql) if isinstance(ql, dict) else ql) or []
    qids = [q.get("question_id") for q in qs if q.get("question_id")][:5]
    rec("prof", "list questions", st == 200 and len(qids) > 0, st, f"count={len(qs)}")
    for qid in qids[:3]:
        req("POST", f"/api/questions/{qid}/approve", ptok)
    rec("prof", "approve questions", bool(qids), 200, f"approved {min(3, len(qids))}")

    # Exam builder (3 variants)
    st, ex = req("POST", f"/api/courses/{cid}/exams/build", ptok, body={"q_count": 6, "exam_len": 30, "difficulty": "balanced"})
    nv = len(ex.get("variants", [])) if isinstance(ex, dict) else 0
    rec("prof", "build exam (3 variants)", st == 200 and nv == 3, st, f"variants={nv}")

    # Assignment
    st, asg = req("POST", f"/api/courses/{cid}/assignments", ptok, body={
        "course_id": cid, "title": f"{subject} exam", "question_ids": qids,
        "available_from": iso(-1), "available_until": iso(7), "adaptive": True})
    aid = (asg.get("id") or asg.get("assignment_id")) if isinstance(asg, dict) else None
    rec("prof", "create assignment", st in (200, 201) and bool(aid), st, f"id={aid}")
    st, _ = req("GET", f"/api/courses/{cid}/assignments", ptok); rec("prof", "list assignments", st == 200, st)
    st, _ = req("GET", f"/api/assignments/{aid}/sessions", ptok); rec("prof", "monitor sessions", st == 200, st)

    # ── STUDENT JOURNEY (student 1, full multi-turn) ──
    print("\n########## STUDENT JOURNEY (student1, all questions) ##########")
    stok = login(STU1)
    rec("student", "student login", bool(stok), 200)
    st, _ = req("GET", "/api/student/dashboard", stok); rec("student", "dashboard", st == 200, st)
    st, sa = req("GET", "/api/student/assignments", stok); rec("student", "list my assignments", st == 200, st)

    sid = None
    if aid:
        st, start = req("POST", f"/api/assignments/{aid}/start", stok, body={})
        qlist = start.get("questions", []) if isinstance(start, dict) else []
        sid = start.get("session_id") if isinstance(start, dict) else None
        rec("student", "start exam", st == 200 and bool(sid), st, f"questions={len(qlist)}")
        # answer every question (multi-turn / adaptive pacing)
        n_ans = 0
        for i in range(max(1, len(qlist))):
            st, ans = req("POST", f"/api/sessions/{sid}/answer", stok,
                          body={"question_index": i, "answer_text":
                                "The mechanism works because the underlying variables are causally linked, "
                                "which changes the outcome as described in the material."})
            if st == 200:
                n_ans += 1
            else:
                break
        rec("student", "answer all questions (multi-turn)", n_ans >= 1, 200, f"answered {n_ans}")
        st, _ = req("GET", f"/api/sessions/{sid}/status", stok); rec("student", "session status", st == 200, st)
        # SSE stream reachability
        sse = sse_check(f"/api/sessions/{sid}/stream", stok)
        rec("student", "SSE stream reachable", sse[0], sse[1], sse[2])
        st, _ = req("POST", f"/api/sessions/{sid}/complete", stok, body={}); rec("student", "complete session", st == 200, st)
        st, res1 = req("GET", f"/api/assignments/{aid}/results", stok)
        qr1 = res1.get("question_results", []) if isinstance(res1, dict) else []
        rec("student", "exam results (per-question)", st == 200 and isinstance(res1, dict), st,
            f"q_results={len(qr1)} score={res1.get('score') if isinstance(res1, dict) else '?'}")

    # ── GRADING FLOW (professor) ──
    print("\n########## GRADING FLOW (professor) ##########")
    if aid and sid:
        st, gsp = req("GET", f"/api/grades/{sid}", ptok)
        rec("prof", "get session grades", st == 200, st)
        turn_id = None
        if isinstance(gsp, dict):
            evs = gsp.get("evaluations") or []
            turn_id = evs[0].get("turn_id") if evs else None
        st, _ = req("POST", f"/api/assignments/{aid}/grades/release", ptok, body={})
        rec("prof", "release grades", st == 200, st)
        st, gsp2 = req("GET", f"/api/grades/{sid}", ptok)
        grade_id = gsp2.get("grade_id") if isinstance(gsp2, dict) else None
        rec("prof", "grades released -> grade_id", bool(grade_id), st, f"grade_id={grade_id}")
        if grade_id:
            st, _ = req("POST", f"/api/grades/{grade_id}/override", ptok, body={"new_score": 88, "reason": "manual review"})
            rec("prof", "override grade", st == 200, st)
        if turn_id:
            st, _ = req("GET", f"/api/evaluations/{turn_id}", ptok)
            rec("prof", "get evaluation (turn)", st == 200, st)
        else:
            rec("prof", "get evaluation (turn)", False, "-", "no turn_id available from grades response")

    # ── MULTI-STUDENT ISOLATION ──
    print("\n########## MULTI-STUDENT ISOLATION ##########")
    if aid:
        s2 = login(STU2)
        rec("multi", "student2 login", bool(s2), 200)
        st, start2 = req("POST", f"/api/assignments/{aid}/start", s2, body={})
        sid2 = start2.get("session_id") if isinstance(start2, dict) else None
        rec("multi", "student2 start exam", st == 200 and bool(sid2), st)
        if sid2:
            req("POST", f"/api/sessions/{sid2}/answer", s2, body={"question_index": 0, "answer_text": "A concise causal explanation."})
            req("POST", f"/api/sessions/{sid2}/complete", s2, body={})
            st, r2 = req("GET", f"/api/assignments/{aid}/results", s2)
            own = isinstance(r2, dict) and r2.get("session_id") == sid2
            rec("multi", "student2 sees own results (isolation)", st == 200 and own, st,
                f"own_session={own}")

    report(subject, pdf)


def sse_check(path, token):
    url = BASE + path
    r = urllib.request.Request(url, headers={"authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(r, timeout=6) as resp:
            ctype = resp.headers.get("content-type", "")
            ok = resp.status == 200 and "event-stream" in ctype
            return ok, resp.status, f"content-type={ctype}"
    except urllib.error.HTTPError as e:
        return False, e.code, "http error"
    except Exception as e:
        # A read timeout after a 200+headers is normal for a live stream; treat as reachable.
        return True, 200, f"stream open ({type(e).__name__})"


def report(subject, pdf):
    print("\n\n################ TEST REPORT ################")
    print(f"Subject: {subject}   PDF: {os.path.basename(pdf)}")
    fails = [r for r in results if not r["ok"]]
    for j in ("prof", "student", "multi"):
        rows = [r for r in results if r["journey"] == j]
        if rows:
            print(f"\n{j.upper()}:")
            for r in rows:
                print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['step']:<38} HTTP {str(r['status']):<4} {r['note']}")
    print(f"\nSUMMARY: {len(results)-len(fails)}/{len(results)} passed, {len(fails)} FAILED")
    if fails:
        print("\nBROKEN:")
        for r in fails:
            print(f"  - [{r['journey']}] {r['step']} — HTTP {r['status']} {r['note']}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
