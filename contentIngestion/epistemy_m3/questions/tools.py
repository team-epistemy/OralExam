"""M5 question tools: generate, list, approve, reject, get questions."""
from __future__ import annotations
from typing import List, Optional

from epistemy_m3.models import Caller, Role
from epistemy_m3.questions.models import (
    QuestionCandidate, QuestionStatus, QuestionType,
    GenerationRequest, GenerationResult, GenerationJobStatus,
)
from epistemy_m3.questions.generator import QuestionGenerator
from epistemy_m3.api.service import AuthorizationError


class QuestionsTools:
    """MCP/REST tools for question generation and management.

    Read tools require course membership; write tools require professor role.
    """

    def __init__(self, repo, generator: QuestionGenerator, is_member):
        self.repo = repo
        self.generator = generator
        self.is_member = is_member

    def generate_questions(
        self,
        caller: Caller,
        course_id: str,
        concept_ids: Optional[List[str]] = None,
        count: int = 5,
        question_type: QuestionType = QuestionType.FREE_TEXT,
    ) -> GenerationResult:
        """Generate candidate questions for a course. Professor role required.

        Kicks off question generation using the concept graph and corpus.
        Returns immediately with a job tracking object; questions are
        available once the job completes.
        """
        self._require_professor(caller, course_id)
        self.repo.set_tenant(caller.org_id)

        # Generate questions synchronously for small batches
        candidates = self.generator.generate(
            course_id=course_id,
            org_id=caller.org_id,
            concept_ids=concept_ids,
            count=count,
            question_type=question_type,
            created_by=caller.user_id,
        )

        # Persist each generated question
        for q in candidates:
            self.repo.create_question(q)

        return GenerationResult(
            job_id="sync",
            status=GenerationJobStatus.SUCCEEDED,
            generated_count=len(candidates),
            questions=candidates,
        )

    def list_questions(
        self,
        caller: Caller,
        course_id: str,
        status: Optional[QuestionStatus] = None,
    ) -> List[QuestionCandidate]:
        """List questions in a course, optionally filtered by status."""
        self._require_member(caller, course_id)
        self.repo.set_tenant(caller.org_id)
        return self.repo.list_questions(course_id, status=status)

    def get_question(self, caller: Caller, question_id: str) -> QuestionCandidate:
        """Retrieve a single question by ID."""
        self.repo.set_tenant(caller.org_id)
        question = self.repo.get_question(question_id)
        if not question:
            raise AuthorizationError("question not found")
        self._require_member(caller, question.course_id)
        return question

    def approve_question(self, caller: Caller, question_id: str) -> QuestionCandidate:
        """Approve a draft question for use in assignments. Professor only."""
        self.repo.set_tenant(caller.org_id)
        question = self.repo.get_question(question_id)
        if not question:
            raise AuthorizationError("question not found")
        self._require_professor(caller, question.course_id)
        if question.status != QuestionStatus.DRAFT:
            raise AuthorizationError(
                f"cannot approve question in status '{question.status.value}'"
            )
        self.repo.update_question_status(question_id, QuestionStatus.APPROVED)
        question.status = QuestionStatus.APPROVED
        return question

    def reject_question(self, caller: Caller, question_id: str) -> QuestionCandidate:
        """Reject a draft question. Professor only."""
        self.repo.set_tenant(caller.org_id)
        question = self.repo.get_question(question_id)
        if not question:
            raise AuthorizationError("question not found")
        self._require_professor(caller, question.course_id)
        if question.status != QuestionStatus.DRAFT:
            raise AuthorizationError(
                f"cannot reject question in status '{question.status.value}'"
            )
        self.repo.update_question_status(question_id, QuestionStatus.REJECTED)
        question.status = QuestionStatus.REJECTED
        return question

    def _require_professor(self, caller: Caller, course_id: str) -> None:
        """Reject non-professors."""
        if caller.role != Role.PROFESSOR or not self.is_member(caller, course_id):
            raise AuthorizationError("professor role on this course required")

    def _require_member(self, caller: Caller, course_id: str) -> None:
        """Any course membership suffices for read tools."""
        if not self.is_member(caller, course_id):
            raise AuthorizationError("course membership required")
