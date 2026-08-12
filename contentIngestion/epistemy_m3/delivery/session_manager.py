"""M6 SessionManager: orchestrates exam delivery from start to completion."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from epistemy_m3.delivery.models import (
    Assignment, AssignmentStatus, ExamSession, SessionStatus,
    SessionTurn, SessionState,
)
from epistemy_m3.delivery.adaptive import AdaptiveSelector

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages exam session lifecycle: start, deliver questions, submit answers.

    Coordinates between the assignment configuration, question pool,
    adaptive selector, and session persistence.
    """

    def __init__(self, repo, adaptive_selector: AdaptiveSelector):
        """Initialize the session manager.

        Args:
            repo: Repository with session/assignment/question persistence methods.
            adaptive_selector: The adaptive question selection engine.
        """
        self.repo = repo
        self.selector = adaptive_selector

    def start_session(
        self, assignment_id: str, student_id: str, org_id: str
    ) -> SessionState:
        """Start a new exam session for a student on an assignment.

        Validates the assignment is active and within time window,
        checks for existing active sessions (resumes if allowed),
        creates a new session, and delivers the first question.
        """
        assignment = self.repo.get_assignment(assignment_id)
        if not assignment:
            raise SessionError("assignment not found")

        # Validate assignment is active
        if assignment.status != AssignmentStatus.ACTIVE:
            raise SessionError(
                f"assignment is not active (status: {assignment.status.value})"
            )

        # Validate time window
        now = datetime.now(timezone.utc)
        config = assignment.config
        if config.time_window_start and now < config.time_window_start:
            raise SessionError("assignment has not opened yet")
        if config.time_window_end and now > config.time_window_end:
            raise SessionError("assignment has closed")

        # Check for existing active session (resume if allowed)
        existing = self.repo.get_active_session(assignment_id, student_id)
        if existing:
            if config.allow_resume:
                return self._build_session_state(existing, assignment)
            raise SessionError("active session already exists; resume not allowed")

        # Create new session
        session = ExamSession(
            assignment_id=assignment_id,
            student_id=student_id,
            org_id=org_id,
            course_id=assignment.course_id,
        )
        self.repo.create_session(session)

        # Deliver first question
        self._deliver_next_question(session, assignment)

        return self._build_session_state(session, assignment)

    def get_next_question(self, session_id: str) -> SessionState:
        """Get the current/next question for an active session.

        If the current turn is unanswered, returns it again.
        If all turns are answered, selects the next question adaptively.
        """
        session = self._get_active_session(session_id)
        assignment = self.repo.get_assignment(session.assignment_id)

        # Check if current turn exists and is unanswered
        current_turn = self.repo.get_current_turn(session_id, session.current_turn_index)
        if current_turn and current_turn.student_answer is None:
            return self._build_session_state(session, assignment)

        # All prior turns answered — select and deliver next question
        if session.current_turn_index >= assignment.config.max_questions:
            self._complete_session(session)
            return self._build_session_state(session, assignment)

        self._deliver_next_question(session, assignment)
        return self._build_session_state(session, assignment)

    def submit_answer(
        self,
        session_id: str,
        answer_text: str,
        time_spent_seconds: Optional[int] = None,
    ) -> SessionTurn:
        """Submit a student answer for the current turn.

        Records the answer, updates the turn, and advances the session.
        Returns the completed turn for downstream evaluation.
        """
        session = self._get_active_session(session_id)

        # Get current unanswered turn
        current_turn = self.repo.get_current_turn(
            session_id, session.current_turn_index
        )
        if not current_turn:
            raise SessionError("no active question to answer")
        if current_turn.student_answer is not None:
            raise SessionError("current question already answered")

        # Record the answer
        now = datetime.now(timezone.utc)
        current_turn.student_answer = answer_text
        current_turn.answered_at = now
        current_turn.time_spent_seconds = time_spent_seconds
        self.repo.update_turn(current_turn)

        # Update covered concepts from this question
        question = self.repo.get_question(current_turn.question_id)
        if question and question.concept_ids:
            new_covered = set(session.concepts_covered) | set(question.concept_ids)
            session.concepts_covered = list(new_covered)

        # Advance turn index
        session.current_turn_index += 1
        self.repo.update_session(session)

        # Check if we've hit max questions
        assignment = self.repo.get_assignment(session.assignment_id)
        if session.current_turn_index >= assignment.config.max_questions:
            self._complete_session(session)

        return current_turn

    def resume_session(self, session_id: str) -> SessionState:
        """Resume an active session, returning its current state."""
        session = self._get_active_session(session_id)
        assignment = self.repo.get_assignment(session.assignment_id)
        return self._build_session_state(session, assignment)

    def abandon_session(self, session_id: str) -> SessionState:
        """Mark a session as abandoned."""
        session = self._get_active_session(session_id)
        session.status = SessionStatus.ABANDONED
        session.completed_at = datetime.now(timezone.utc)
        self.repo.update_session(session)
        assignment = self.repo.get_assignment(session.assignment_id)
        return self._build_session_state(session, assignment)

    def _deliver_next_question(
        self, session: ExamSession, assignment: Assignment
    ) -> Optional[SessionTurn]:
        """Use the adaptive selector to pick and deliver the next question."""
        # Get the question pool for this assignment
        questions = self.repo.get_questions_for_set(assignment.question_set_id)
        if not questions:
            logger.warning(
                "No questions in set %s for assignment %s",
                assignment.question_set_id, assignment.assignment_id,
            )
            self._complete_session(session)
            return None

        # Build session history for the adaptive selector
        history = self._build_history(session)
        covered = set(session.concepts_covered)

        # Convert questions to the format expected by the selector
        available = [
            {
                "question_id": q.question_id,
                "concept_ids": q.concept_ids,
                "difficulty": q.difficulty.model_dump() if q.difficulty else {},
                "text": q.text,
            }
            for q in questions
        ]

        # Select next question
        selected = self.selector.select_next(
            available_questions=available,
            covered_concepts=covered,
            session_history=history,
            max_questions=assignment.config.max_questions,
        )

        if not selected:
            self._complete_session(session)
            return None

        # Create the turn
        turn = SessionTurn(
            session_id=session.session_id,
            turn_index=session.current_turn_index,
            question_id=selected["question_id"],
        )
        self.repo.create_turn(turn)

        # Track delivered questions
        session.questions_delivered.append(selected["question_id"])
        self.repo.update_session(session)

        return turn

    def _build_history(self, session: ExamSession) -> List[Dict[str, Any]]:
        """Build the session history for the adaptive selector."""
        turns = self.repo.list_turns(session.session_id)
        history = []
        for turn in turns:
            entry = {
                "question_id": turn.question_id,
                "answered": turn.student_answer is not None,
            }
            # Include evaluation scores if available
            evaluation = self.repo.get_evaluation_for_turn(turn.turn_id)
            if evaluation:
                entry["eds_score"] = evaluation.get("eds_score")
            history.append(entry)
        return history

    def _complete_session(self, session: ExamSession) -> None:
        """Mark the session as completed."""
        session.status = SessionStatus.COMPLETED
        session.completed_at = datetime.now(timezone.utc)
        self.repo.update_session(session)

    def _build_session_state(
        self, session: ExamSession, assignment: Assignment
    ) -> SessionState:
        """Build the client-facing session state response."""
        current_question = None
        if session.status == SessionStatus.ACTIVE:
            turn = self.repo.get_current_turn(
                session.session_id, session.current_turn_index
            )
            if turn:
                question = self.repo.get_question(turn.question_id)
                if question:
                    current_question = {
                        "question_id": question.question_id,
                        "text": question.text,
                        "question_type": question.question_type.value,
                        "turn_index": turn.turn_index,
                    }

        # Calculate time remaining if time limit is set
        time_remaining = None
        if assignment.config.time_limit_minutes and session.status == SessionStatus.ACTIVE:
            elapsed = (datetime.now(timezone.utc) - session.started_at).total_seconds()
            limit = assignment.config.time_limit_minutes * 60
            time_remaining = max(0, int(limit - elapsed))

        return SessionState(
            session_id=session.session_id,
            assignment_id=session.assignment_id,
            status=session.status,
            current_turn_index=session.current_turn_index,
            total_questions=assignment.config.max_questions,
            current_question=current_question,
            time_remaining_seconds=time_remaining,
        )

    def _get_active_session(self, session_id: str) -> ExamSession:
        """Load a session and verify it's active."""
        session = self.repo.get_session(session_id)
        if not session:
            raise SessionError("session not found")
        if session.status != SessionStatus.ACTIVE:
            raise SessionError(f"session is not active (status: {session.status.value})")
        return session


class SessionError(Exception):
    """Raised when a session operation cannot proceed."""
    pass
