"""M6 delivery tools: create assignments, start exams, submit answers."""
from __future__ import annotations
from typing import List, Optional

from backend.models import Caller, Role
from backend.delivery.models import (
    Assignment, AssignmentStatus, AssignmentConfig,
    SessionState, SessionTurn,
)
from backend.delivery.session_manager import SessionManager
from backend.api.service import AuthorizationError


class DeliveryTools:
    """MCP/REST tools for assignment delivery and exam sessions.

    Assignment management requires professor role.
    Exam session operations require course membership (students take exams).
    """

    def __init__(self, repo, session_manager: SessionManager, is_member):
        self.repo = repo
        self.session_manager = session_manager
        self.is_member = is_member

    def create_assignment(
        self,
        caller: Caller,
        course_id: str,
        title: str,
        question_set_id: str,
        config: Optional[dict] = None,
    ) -> Assignment:
        """Create a new assignment. Professor role required.

        Args:
            caller: The authenticated caller.
            course_id: Course to create the assignment in.
            title: Human-readable assignment title.
            question_set_id: The question set to draw from.
            config: Optional delivery configuration overrides.
        """
        self._require_professor(caller, course_id)
        self.repo.set_tenant(caller.org_id)

        # Validate question set exists and belongs to this course
        question_set = self.repo.get_question_set(question_set_id)
        if not question_set or question_set.course_id != course_id:
            raise AuthorizationError("question set not found in this course")

        assignment_config = AssignmentConfig(**(config or {}))
        assignment = Assignment(
            course_id=course_id,
            org_id=caller.org_id,
            title=title,
            question_set_id=question_set_id,
            config=assignment_config,
            created_by=caller.user_id,
        )
        self.repo.create_assignment(assignment)
        return assignment

    def activate_assignment(self, caller: Caller, assignment_id: str) -> Assignment:
        """Activate a draft assignment so students can start sessions."""
        self.repo.set_tenant(caller.org_id)
        assignment = self.repo.get_assignment(assignment_id)
        if not assignment:
            raise AuthorizationError("assignment not found")
        self._require_professor(caller, assignment.course_id)
        if assignment.status != AssignmentStatus.DRAFT:
            raise AuthorizationError(
                f"cannot activate assignment in status '{assignment.status.value}'"
            )
        assignment.status = AssignmentStatus.ACTIVE
        self.repo.update_assignment(assignment)
        return assignment

    def close_assignment(self, caller: Caller, assignment_id: str) -> Assignment:
        """Close an active assignment to prevent new sessions."""
        self.repo.set_tenant(caller.org_id)
        assignment = self.repo.get_assignment(assignment_id)
        if not assignment:
            raise AuthorizationError("assignment not found")
        self._require_professor(caller, assignment.course_id)
        if assignment.status != AssignmentStatus.ACTIVE:
            raise AuthorizationError(
                f"cannot close assignment in status '{assignment.status.value}'"
            )
        assignment.status = AssignmentStatus.CLOSED
        self.repo.update_assignment(assignment)
        return assignment

    def start_exam(self, caller: Caller, assignment_id: str) -> SessionState:
        """Start a new exam session for the caller (student).

        The student must be a member of the assignment's course.
        """
        self.repo.set_tenant(caller.org_id)
        assignment = self.repo.get_assignment(assignment_id)
        if not assignment:
            raise AuthorizationError("assignment not found")
        self._require_member(caller, assignment.course_id)

        return self.session_manager.start_session(
            assignment_id=assignment_id,
            student_id=caller.user_id,
            org_id=caller.org_id,
        )

    def submit_answer(
        self,
        caller: Caller,
        session_id: str,
        answer_text: str,
        time_spent_seconds: Optional[int] = None,
    ) -> SessionTurn:
        """Submit an answer for the current question in the session."""
        self.repo.set_tenant(caller.org_id)
        session = self.repo.get_session(session_id)
        if not session:
            raise AuthorizationError("session not found")
        if session.student_id != caller.user_id:
            raise AuthorizationError("not your session")

        return self.session_manager.submit_answer(
            session_id=session_id,
            answer_text=answer_text,
            time_spent_seconds=time_spent_seconds,
        )

    def get_session_status(self, caller: Caller, session_id: str) -> SessionState:
        """Get the current state of a session."""
        self.repo.set_tenant(caller.org_id)
        session = self.repo.get_session(session_id)
        if not session:
            raise AuthorizationError("session not found")
        # Students can see their own sessions; professors can see all
        if caller.role != Role.PROFESSOR and session.student_id != caller.user_id:
            raise AuthorizationError("access denied")
        self._require_member(caller, session.course_id)

        assignment = self.repo.get_assignment(session.assignment_id)
        return self.session_manager._build_session_state(session, assignment)

    def list_assignments(
        self,
        caller: Caller,
        course_id: str,
        status: Optional[AssignmentStatus] = None,
    ) -> List[Assignment]:
        """List assignments in a course, optionally filtered by status."""
        self._require_member(caller, course_id)
        self.repo.set_tenant(caller.org_id)
        return self.repo.list_assignments(course_id, status=status)

    def _require_professor(self, caller: Caller, course_id: str) -> None:
        """Reject non-professors."""
        if caller.role != Role.PROFESSOR or not self.is_member(caller, course_id):
            raise AuthorizationError("professor role on this course required")

    def _require_member(self, caller: Caller, course_id: str) -> None:
        """Any course membership suffices for read/student tools."""
        if not self.is_member(caller, course_id):
            raise AuthorizationError("course membership required")
