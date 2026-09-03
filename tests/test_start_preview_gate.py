"""Gate for starting an exam session: students on active, professors on own drafts."""
import pytest

pytest.importorskip("fastapi")  # http_app imports fastapi at module load

from backend.app.http_app import _start_is_preview
from backend.models import Role
from backend.api.service import AuthorizationError


def test_active_assignment_is_not_preview():
    assert _start_is_preview("active", "prof-1", Role.STUDENT, "stud-9") is False
    assert _start_is_preview("active", "prof-1", Role.PROFESSOR, "prof-1") is False


def test_owning_professor_can_start_draft_as_preview():
    assert _start_is_preview("draft", "prof-1", Role.PROFESSOR, "prof-1") is True


def test_student_cannot_start_draft():
    with pytest.raises(AuthorizationError):
        _start_is_preview("draft", "prof-1", Role.STUDENT, "stud-9")


def test_non_owner_professor_cannot_start_draft():
    with pytest.raises(AuthorizationError):
        _start_is_preview("draft", "prof-1", Role.PROFESSOR, "prof-2")


def test_closed_assignment_rejected():
    with pytest.raises(AuthorizationError):
        _start_is_preview("closed", "prof-1", Role.STUDENT, "stud-9")
