// Human-readable labels for an assignment's type, kept in one place so the
// student flow always names the task correctly — a practice test, an exam, or
// an assignment — rather than calling everything an "Exam".
//
// Reconstructed for the release cherry-pick: Uthira's feat/student-grade-visibility
// commit 12816f2 imports typeNoun/typeNounLower from here, but the module itself
// was never committed to that branch. The mapping mirrors the prior inline label
// (isPractice ? 'Practice Test' : type === 'exam' ? 'Exam' : 'Assignment').

export type AssignmentType = 'practice' | 'exam' | 'assignment' | (string & {});

export function typeNoun(type: AssignmentType | null | undefined): string {
  if (type === 'practice') return 'Practice Test';
  if (type === 'exam') return 'Exam';
  return 'Assignment';
}

export function typeNounLower(type: AssignmentType | null | undefined): string {
  return typeNoun(type).toLowerCase();
}
