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
COURSE_GET = "/api/courses/{course_id}"

# ── Dashboard routes ─────────────────────────────────────────────────────────
PROFESSOR_DASHBOARD = "/api/professor/dashboard"
PROFESSOR_COURSES = "/api/professor/courses"
STUDENT_DASHBOARD = "/api/student/dashboard"
STUDENT_ASSIGNMENTS = "/api/student/assignments"

# ── M4 Graph routes ──────────────────────────────────────────────────────────
GRAPH_GET = "/api/courses/{course_id}/graph"
GRAPH_REBUILD = "/api/courses/{course_id}/graph/rebuild"
GRAPH_NEIGHBORS = "/api/graph/{concept_id}/neighbors"

# ── M5 Question routes ───────────────────────────────────────────────────────
QUESTIONS_LIST = "/api/courses/{course_id}/questions"
QUESTIONS_GENERATE = "/api/courses/{course_id}/questions/generate"
QUESTION_GET = "/api/questions/{question_id}"
QUESTION_UPDATE = "/api/questions/{question_id}"
QUESTION_APPROVE = "/api/questions/{question_id}/approve"
QUESTION_REJECT = "/api/questions/{question_id}/reject"
EXAM_BUILD = "/api/courses/{course_id}/exams/build"
EXAM_ASSIGN = "/api/courses/{course_id}/exams/assign"

# ── Text-to-speech (ElevenLabs proxy) ────────────────────────────────────────
TTS = "/api/tts"

# ── M6 Delivery routes ──────────────────────────────────────────────────────
ASSIGNMENTS_LIST = "/api/courses/{course_id}/assignments"
ASSIGNMENT_GET = "/api/assignments/{assignment_id}"
ASSIGNMENT_SESSIONS = "/api/assignments/{assignment_id}/sessions"
ASSIGNMENT_CLOSE = "/api/assignments/{assignment_id}/close"
ASSIGNMENT_DELETE = "/api/assignments/{assignment_id}"
MATERIAL_DELETE = "/api/materials/{material_id}"
ASSIGNMENT_START = "/api/assignments/{assignment_id}/start"
ASSIGNMENT_RESULTS = "/api/assignments/{assignment_id}/results"
ASSIGNMENT_CASE = "/api/assignments/{assignment_id}/case"
SESSION_ANSWER = "/api/sessions/{session_id}/answer"
SESSION_COMPLETE = "/api/sessions/{session_id}/complete"
SESSION_STATUS = "/api/sessions/{session_id}/status"
SESSION_STREAM = "/api/sessions/{session_id}/stream"

# ── M7 Evaluation routes ────────────────────────────────────────────────────
EVALUATION_GET = "/api/evaluations/{turn_id}"
GRADES_SESSION = "/api/grades/{session_id}"
GRADES_RELEASE = "/api/assignments/{assignment_id}/grades/release"
GRADE_OVERRIDE = "/api/grades/{grade_id}/override"


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
