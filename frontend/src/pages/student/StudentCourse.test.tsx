import { describe, it, expect } from 'vitest';
import { attemptChip, timeLimit } from './StudentCourse';
import type { StudentAssignment } from './Dashboard';

const base: StudentAssignment = {
  id: 'a1', title: 'T', course_id: 'c1', course_name: 'CS101', status: 'active',
  assignment_type: 'exam', config: {}, created_at: '2026-01-01',
};

describe('attemptChip', () => {
  it('shows nothing until the student has taken it', () => {
    expect(attemptChip({ ...base, completed: false }, 'exam')).toBeNull();
  });

  it('ends at Completed for a practice test — it is never graded', () => {
    // grade_status is irrelevant for practice; it must not say "Awaiting grade".
    expect(attemptChip({ ...base, assignment_type: 'practice', completed: true }, 'practice')?.text)
      .toBe('Completed');
  });

  it('says Awaiting grade once a graded item is taken but not released', () => {
    expect(attemptChip({ ...base, completed: true }, 'exam')?.text).toBe('Awaiting grade');
    expect(attemptChip({ ...base, completed: true, grade_status: 'pending' }, 'assignment')?.text)
      .toBe('Awaiting grade');
  });

  it('says Graded — without the score — once released', () => {
    const chip = attemptChip({ ...base, completed: true, grade_status: 'released' }, 'exam');
    expect(chip?.text).toBe('Graded');
    expect(chip?.text).not.toMatch(/\d/);  // the number stays behind Results
  });
});

describe('timeLimit', () => {
  it('reads the stored time_limit_minutes', () => {
    expect(timeLimit({ ...base, config: { time_limit_minutes: 45, difficulty: 'balanced' } })).toBe(45);
  });

  it('falls back to the older duration_minutes key', () => {
    expect(timeLimit({ ...base, config: { duration_minutes: 30 } })).toBe(30);
  });

  it('is null for an untimed item, so the card shows no duration', () => {
    expect(timeLimit({ ...base, config: { difficulty: 'balanced' } })).toBeNull();
    expect(timeLimit({ ...base, config: { time_limit_minutes: 0 } })).toBeNull();
  });
});
