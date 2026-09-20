"""Single source of truth for HTTP route paths and header names.

Both the FastAPI routes and the demo frontend (via /config) read from here,
so a path change is one edit in one file.
"""
from __future__ import annotations

# ── Header names ─────────────────────────────────────────────────────────────
HDR_ORG_NAME = "x-org-name"
HDR_USER_ID = "x-user-id"
HDR_ROLE = "x-role"

# ── M3 Route paths (existing) ───────────────────────────────────────────────
HEALTH = "/health"
PRESIGN = "/materials:presign"
REGISTER = "/versions/{version_id}/register"
LIST_MATERIALS = "/orgs/{org_name}/courses/{course_name}/materials"
LIST_VERSIONS = "/materials/{material_id}/versions"
MATERIAL_VIEW = "/api/materials/{material_id}/view"
SEARCH_CORPUS = "/courses/{course_id}/search"

# ── Course routes ────────────────────────────────────────────────────────────
COURSE_GET = "/api/courses/{course_id}"          # GET detail, DELETE removes course
COURSE_CREATE = "/api/professor/courses"          # POST creates a course
COURSE_STUDENTS = "/api/courses/{course_id}/students"   # GET roster, POST enroll
COURSE_PERFORMANCE = "/api/courses/{course_id}/performance"  # GET anonymized practice analytics
COURSE_SESSIONS = "/api/courses/{course_id}/sessions"                 # GET list, POST create
COURSE_SESSION = "/api/courses/{course_id}/sessions/{session_id}"     # PUT update, DELETE, DELETE unenroll
COURSE_SYLLABUS = "/api/courses/{course_id}/syllabus"   # GET syllabus, POST mark a material as syllabus
COURSE_SYLLABUS_PROCESS = "/api/courses/{course_id}/syllabus/process"  # POST: parse syllabus -> sessions

# ── Dashboard routes ─────────────────────────────────────────────────────────
PROFESSOR_DASHBOARD = "/api/professor/dashboard"
PROFESSOR_COURSES = "/api/professor/courses"
STUDENT_DASHBOARD = "/api/student/dashboard"
STUDENT_ASSIGNMENTS = "/api/student/assignments"

# ── M4 Graph routes ──────────────────────────────────────────────────────────
GRAPH_GET = "/api/courses/{course_id}/graph"
GRAPH_REBUILD = "/api/courses/{course_id}/graph/rebuild"
GRAPH_CONCEPTS = "/api/courses/{course_id}/graph/concepts"  # PUT: persist curated concept set
GRAPH_DOCUMENTS = "/api/courses/{course_id}/graph/documents"  # GET: docs that have a per-document concept graph
MATERIAL_GRAPH = "/api/materials/{material_version_id}/graph"  # GET: one document's concept graph
MATERIAL_GRAPH_BUILD = "/api/courses/{course_id}/materials/{material_version_id}/graph/build"  # POST: (re)build one doc's graph
GRAPH_NEIGHBORS = "/api/graph/{concept_id}/neighbors"

# ── M5 Question routes ───────────────────────────────────────────────────────
QUESTIONS_LIST = "/api/courses/{course_id}/questions"
QUESTIONS_GENERATE = "/api/courses/{course_id}/questions/generate"
QUESTION_GET = "/api/questions/{question_id}"
QUESTION_UPDATE = "/api/questions/{question_id}"
QUESTION_APPROVE = "/api/questions/{question_id}/approve"
QUESTION_REJECT = "/api/questions/{question_id}/reject"
EXAM_BUILD = "/api/courses/{course_id}/exams/build"
EXAM_REGENERATE = "/api/courses/{course_id}/exams/regenerate"  # POST: fresh LLM-authored questions
EXAM_ASSIGN = "/api/courses/{course_id}/exams/assign"

# ── Text-to-speech (ElevenLabs proxy) ────────────────────────────────────────
TTS = "/api/tts"

# ── M6 Delivery routes ──────────────────────────────────────────────────────
ASSIGNMENTS_LIST = "/api/courses/{course_id}/assignments"
ASSIGNMENT_GET = "/api/assignments/{assignment_id}"
ASSIGNMENT_SESSIONS = "/api/assignments/{assignment_id}/sessions"
ASSIGNMENT_CLOSE = "/api/assignments/{assignment_id}/close"
ASSIGNMENT_DELETE = "/api/assignments/{assignment_id}"
ASSIGNMENT_PUBLISH = "/api/assignments/{assignment_id}/publish"   # POST: draft -> active (professor)
ASSIGNMENT_DISCARD = "/api/assignments/{assignment_id}/discard"   # POST: delete a draft (professor)
MATERIAL_DELETE = "/api/materials/{material_id}"
ASSIGNMENT_START = "/api/assignments/{assignment_id}/start"
ASSIGNMENT_RESULTS = "/api/assignments/{assignment_id}/results"
ASSIGNMENT_CASE = "/api/assignments/{assignment_id}/case"
ASSIGNMENT_PREVIEW = "/api/assignments/{assignment_id}/preview"  # GET: read-only student-view data for a professor (no session)
SESSION_ANSWER = "/api/sessions/{session_id}/answer"
SESSION_COMPLETE = "/api/sessions/{session_id}/complete"
SESSION_STATUS = "/api/sessions/{session_id}/status"
SESSION_STREAM = "/api/sessions/{session_id}/stream"

# ── M7 Evaluation routes ────────────────────────────────────────────────────
EVALUATION_GET = "/api/evaluations/{turn_id}"
GRADES_SESSION = "/api/grades/{session_id}"
GRADES_RELEASE = "/api/assignments/{assignment_id}/grades/release"
GRADE_OVERRIDE = "/api/grades/{grade_id}/override"

# ── Admin: agent-cohort exam simulations ────────────────────────────────────
ADMIN_ACTIVE_TASKS = "/api/admin/active-tasks"                  # GET app background jobs + ECS deploy status
ADMIN_PROFESSORS = "/api/admin/professors"                      # GET professors → courses → enrolled students
ADMIN_PERF_PROBE = "/api/admin/perf/probe"                      # POST start an end-to-end latency probe
ADMIN_PERF_PROBES = "/api/admin/perf/probes"                    # GET saved probe history
ADMIN_PERF_DEFAULTS = "/api/admin/perf/defaults"               # GET actual-implementation param defaults
ADMIN_PERF_PROBE_GET = "/api/admin/perf/probe/{probe_id}"       # GET probe status + results
ADMIN_ASSIGNMENTS = "/api/admin/assignments"                    # GET org assignments (picker)
ADMIN_SIMULATIONS = "/api/admin/simulations"                    # POST create, GET list
ADMIN_SIMULATION = "/api/admin/simulations/{simulation_id}"     # GET status + report
ADMIN_SIMULATION_TURNS = "/api/admin/simulations/{simulation_id}/turns"  # GET transcript rows

# ── Admin: QG (question-generation) quality test bench ──────────────────────
ADMIN_TESTING_SUBJECTS = "/api/admin/testing/subjects"          # GET testable courses (graph-backed)
ADMIN_TESTING_RUNS = "/api/admin/testing/runs"                  # POST run (generate+grade), GET history
ADMIN_TESTING_RUN = "/api/admin/testing/runs/{run_id}"          # GET one run's full report
ADMIN_TESTING_RUN_QUESTIONS = "/api/admin/testing/runs/{run_id}/questions"  # GET per-question + human eval
ADMIN_TESTING_QUESTION_EVAL = "/api/admin/testing/questions/{question_id}/eval"  # PUT human eval

# ── Admin: Examiner Tone Lab (eval endpoints + experiment tracking + governance) ────
ADMIN_EVAL_PROMPT = "/api/admin/eval/examiner-prompt"          # GET live-rendered production examiner prompt
ADMIN_EVAL_CASES = "/api/admin/eval/cases"                     # GET curated question stems + context graphs
ADMIN_EVAL_EXPERIMENTS = "/api/admin/eval/experiments"         # POST save a run (+recommendation), GET history
ADMIN_EVAL_EXPERIMENT = "/api/admin/eval/experiments/{experiment_id}"  # GET one saved experiment
ADMIN_EVAL_OVERRIDES = "/api/admin/eval/prompt-override"       # POST create draft, GET list + active
ADMIN_EVAL_OVERRIDE_APPROVE = "/api/admin/eval/prompt-override/{override_id}/approve"    # POST draft->approved
ADMIN_EVAL_OVERRIDE_ACTIVATE = "/api/admin/eval/prompt-override/{override_id}/activate"  # POST approved->active
ADMIN_EVAL_OVERRIDE_REVERT = "/api/admin/eval/prompt-override/{override_id}/revert"      # POST active->archived (back to default)
ADMIN_EVAL_OVERRIDE_REJECT = "/api/admin/eval/prompt-override/{override_id}/reject"      # POST draft->rejected
ADMIN_EXAMINER_EVAL_MODE = "/api/admin/examiner/eval-mode"     # GET current eval mode, PUT set ('sonnet'|'hybrid')
ADMIN_EXAMINER_TEXT_FIRST = "/api/admin/examiner/text-first"   # PUT set text-first render toggle (bool)

# ── Admin: create credential-free demo links ────────────────────────────────
ADMIN_DEMO_LINKS = "/api/admin/demo-links"                    # POST mint a demo link, GET list
ASSIGNMENT_DEMO_LINK = "/api/assignments/{assignment_id}/demo-link"  # POST: professor mints a demo link for own assignment

# ── Public demo (credential-free, token-scoped, self-authenticating) ─────────
DEMO_META = "/api/demo/{token}"                                # GET demo meta (no attempt consumed)
DEMO_CASE = "/api/demo/{token}/case"                           # GET case materials
DEMO_START = "/api/demo/{token}/start"                         # POST start a demo session (consumes an attempt)
DEMO_ANSWER = "/api/demo/{token}/answer"                       # POST submit an answer
DEMO_STATUS = "/api/demo/{token}/status"                       # GET session status
DEMO_COMPLETE = "/api/demo/{token}/complete"                   # POST complete a demo session
DEMO_TTS = "/api/demo/{token}/tts"                             # POST text-to-speech proxy


def frontend_config() -> dict:
    """Path/header constants served to the browser so it never hardcodes them."""
    return {
        "headers": {"orgName": HDR_ORG_NAME, "userId": HDR_USER_ID,
                    "role": HDR_ROLE},
        "routes": {
            "presign": PRESIGN,
            "register": REGISTER,
            "listVersions": LIST_VERSIONS,
            "listMaterials": LIST_MATERIALS,
            "materialView": MATERIAL_VIEW,
            "searchCorpus": SEARCH_CORPUS,
            "professorDashboard": PROFESSOR_DASHBOARD,
            "professorCourses": PROFESSOR_COURSES,
            "courseCreate": COURSE_CREATE,
            "courseStudents": COURSE_STUDENTS,
            "courseSyllabus": COURSE_SYLLABUS,
            "graphGet": GRAPH_GET,
            "graphRebuild": GRAPH_REBUILD,
            "graphNeighbors": GRAPH_NEIGHBORS,
            "questionsList": QUESTIONS_LIST,
            "questionsGenerate": QUESTIONS_GENERATE,
            "questionGet": QUESTION_GET,
            "questionUpdate": QUESTION_UPDATE,
            "questionApprove": QUESTION_APPROVE,
            "questionReject": QUESTION_REJECT,
            "examBuild": EXAM_BUILD,
            "examAssign": EXAM_ASSIGN,
            "tts": TTS,
            "studentAssignments": STUDENT_ASSIGNMENTS,
            "assignmentsList": ASSIGNMENTS_LIST,
            "assignmentGet": ASSIGNMENT_GET,
            "assignmentStart": ASSIGNMENT_START,
            "assignmentResults": ASSIGNMENT_RESULTS,
            "assignmentCase": ASSIGNMENT_CASE,
            "sessionAnswer": SESSION_ANSWER,
            "sessionStatus": SESSION_STATUS,
            "sessionStream": SESSION_STREAM,
            "evaluationGet": EVALUATION_GET,
            "gradesSession": GRADES_SESSION,
            "gradesRelease": GRADES_RELEASE,
            "gradeOverride": GRADE_OVERRIDE,
        },
    }
