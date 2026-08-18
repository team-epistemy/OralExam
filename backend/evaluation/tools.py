"""M7 evaluation tools: evaluate answers, get results, manage grades."""
from __future__ import annotations
from typing import List, Optional
from datetime import datetime, timezone

from backend.models import Caller, Role
from backend.evaluation.models import (
    Evaluation, EvaluationResult, Grade, GradeStatus, ComponentScores,
)
from backend.evaluation.claim_extractor import ClaimExtractor
from backend.evaluation.scorer import GraphScorer
from backend.api.service import AuthorizationError


class EvaluationTools:
    """MCP/REST tools for evaluation and grading.

    Evaluate tools require course membership; grade management requires professor.
    """

    def __init__(
        self,
        repo,
        claim_extractor: ClaimExtractor,
        scorer: GraphScorer,
        is_member,
    ):
        self.repo = repo
        self.extractor = claim_extractor
        self.scorer = scorer
        self.is_member = is_member

    def evaluate_answer(self, caller: Caller, turn_id: str) -> EvaluationResult:
        """Evaluate a student's answer for a specific turn.

        Extracts claims, maps to concept graph, computes EDS score.
        Requires professor role (evaluations are not student-visible until released).
        """
        self.repo.set_tenant(caller.org_id)

        # Load the turn and its context
        turn = self.repo.get_turn_by_id(turn_id)
        if not turn:
            raise AuthorizationError("turn not found")

        session = self.repo.get_session(turn.session_id)
        if not session:
            raise AuthorizationError("session not found")

        self._require_professor(caller, session.course_id)

        # Check if already evaluated
        existing = self.repo.get_evaluation_by_turn(turn_id)
        if existing:
            return self._evaluation_to_result(existing)

        # Load question for context
        question = self.repo.get_question(turn.question_id)
        if not question:
            raise AuthorizationError("question not found for this turn")

        if not turn.student_answer:
            raise AuthorizationError("turn has no student answer to evaluate")

        # Step 1: Extract claims from the student answer
        claims = self.extractor.extract(
            question_text=question.text,
            student_answer=turn.student_answer,
            concept_ids=question.concept_ids,
        )

        # Step 2: Score against the concept graph
        eds_score, eds_bucket, coverage, component_scores = self.scorer.score(
            claims=claims,
            expected_concept_ids=question.concept_ids,
            question_text=question.text,
        )

        # Step 3: Create and persist the evaluation
        evaluation = Evaluation(
            turn_id=turn_id,
            org_id=session.org_id,
            course_id=session.course_id,
            student_id=session.student_id,
            question_id=turn.question_id,
            claims=claims,
            concept_coverage=coverage,
            eds_score=eds_score,
            eds_bucket=eds_bucket,
            raw_llm_output=claims.raw_llm_output,
        )
        self.repo.create_evaluation(evaluation)

        return self._evaluation_to_result(evaluation)

    def get_evaluation(self, caller: Caller, evaluation_id: str) -> EvaluationResult:
        """Retrieve a specific evaluation by ID."""
        self.repo.set_tenant(caller.org_id)
        evaluation = self.repo.get_evaluation(evaluation_id)
        if not evaluation:
            raise AuthorizationError("evaluation not found")
        self._require_member(caller, evaluation.course_id)
        return self._evaluation_to_result(evaluation)

    def override_grade(
        self,
        caller: Caller,
        grade_id: str,
        new_score: float,
        reason: str,
    ) -> Grade:
        """Override a grade with a professor's manual score. Professor only."""
        self.repo.set_tenant(caller.org_id)
        grade = self.repo.get_grade(grade_id)
        if not grade:
            raise AuthorizationError("grade not found")
        self._require_professor(caller, grade.course_id)

        # Validate score range
        if not 0.0 <= new_score <= 1.0:
            raise AuthorizationError("score must be between 0.0 and 1.0")

        grade.final_score = new_score
        grade.override_by = caller.user_id
        grade.override_reason = reason
        grade.updated_at = datetime.now(timezone.utc)
        self.repo.update_grade(grade)
        return grade

    def release_grades(self, caller: Caller, assignment_id: str) -> List[Grade]:
        """Release all pending grades for an assignment. Professor only."""
        self.repo.set_tenant(caller.org_id)
        assignment = self.repo.get_assignment(assignment_id)
        if not assignment:
            raise AuthorizationError("assignment not found")
        self._require_professor(caller, assignment.course_id)

        # Compute grades for all completed sessions that don't have one yet
        sessions = self.repo.list_completed_sessions(assignment_id)
        released_grades = []

        for session in sessions:
            grade = self.repo.get_grade_for_session(session.session_id)
            if not grade:
                # Compute the grade from evaluations
                grade = self._compute_session_grade(session, assignment_id)
                self.repo.create_grade(grade)

            # Release it
            if grade.status == GradeStatus.PENDING:
                grade.status = GradeStatus.RELEASED
                grade.released_at = datetime.now(timezone.utc)
                grade.updated_at = datetime.now(timezone.utc)
                self.repo.update_grade(grade)
                released_grades.append(grade)

        return released_grades

    def get_student_grades(
        self, caller: Caller, student_id: str, course_id: str
    ) -> List[Grade]:
        """Get all released grades for a student in a course.

        Students can see their own grades; professors can see any student's.
        """
        self._require_member(caller, course_id)
        self.repo.set_tenant(caller.org_id)

        # Students can only view their own grades
        if caller.role != Role.PROFESSOR and caller.user_id != student_id:
            raise AuthorizationError("can only view your own grades")

        return self.repo.list_student_grades(
            student_id=student_id, course_id=course_id
        )

    def _compute_session_grade(self, session, assignment_id: str) -> Grade:
        """Aggregate per-turn evaluations into a session grade."""
        evaluations = self.repo.list_evaluations_for_session(session.session_id)

        if evaluations:
            eval_dicts = [
                {
                    "eds_score": ev.eds_score,
                    "concept_coverage": ev.concept_coverage.model_dump()
                    if hasattr(ev.concept_coverage, "model_dump")
                    else ev.concept_coverage,
                    "component_scores": {
                        "depth_score": ev.eds_score * 0.5,
                        "coherence_score": (ev.claims.causal_claims / max(ev.claims.total_claims, 1))
                        if hasattr(ev.claims, "causal_claims")
                        else 0.0,
                    },
                }
                for ev in evaluations
            ]
            final_score, component_scores = self.scorer.score_session(eval_dicts)
        else:
            final_score = 0.0
            component_scores = ComponentScores()

        return Grade(
            session_id=session.session_id,
            student_id=session.student_id,
            assignment_id=assignment_id,
            org_id=session.org_id,
            course_id=session.course_id,
            final_score=final_score,
            component_scores=component_scores,
        )

    def _evaluation_to_result(self, evaluation: Evaluation) -> EvaluationResult:
        """Convert an Evaluation to the tool output format."""
        return EvaluationResult(
            evaluation_id=evaluation.evaluation_id,
            eds_score=evaluation.eds_score,
            eds_bucket=evaluation.eds_bucket,
            concept_coverage=evaluation.concept_coverage,
            claims_count=evaluation.claims.total_claims,
            causal_claims_count=evaluation.claims.causal_claims,
        )

    def _require_professor(self, caller: Caller, course_id: str) -> None:
        """Reject non-professors."""
        if caller.role != Role.PROFESSOR or not self.is_member(caller, course_id):
            raise AuthorizationError("professor role on this course required")

    def _require_member(self, caller: Caller, course_id: str) -> None:
        """Any course membership suffices for read tools."""
        if not self.is_member(caller, course_id):
            raise AuthorizationError("course membership required")
