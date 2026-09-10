import { describe, it, expect, vi, beforeEach } from 'vitest';
import { downloadTranscriptPdf, examTranscript, resultsTranscript } from './transcript';
import type { ExamResult } from './api/exam';

// Capture every string jsPDF is asked to draw, so the tests can assert what the
// PDF does — and does not — say.
const drawn: string[] = [];
const save = vi.fn();

vi.mock('jspdf', () => ({
  jsPDF: class {
    internal = { pageSize: { getWidth: () => 210, getHeight: () => 297 } };
    text(t: string) { drawn.push(t); }
    splitTextToSize(t: string) { return [t]; }
    setFont() {} setFontSize() {} setTextColor() {} setDrawColor() {}
    setLineWidth() {} line() {} addPage() {} setPage() {}
    getNumberOfPages() { return 1; }
    save = save;
  },
}));

const RESULT: ExamResult = {
  session_id: 's1', assignment_id: 'a1', score: null, grade_released: false,
  total_questions: 5, questions_answered: 2,
  feedback: "Your professor hasn't released your grade yet.",
  completed_at: '2026-09-08T15:04:00Z',
  question_results: [
    { question_id: 'q1', question_text: 'Explain X', answer: 'because Y' },
    { question_id: 'q2', question_text: 'Explain Z', answer: 'because W' },
  ],
};

beforeEach(() => { drawn.length = 0; save.mockClear(); });

describe('resultsTranscript', () => {
  it('carries the questions and the student answers', () => {
    const { entries } = resultsTranscript(RESULT);
    expect(entries).toHaveLength(2);
    expect(entries[0].question).toBe('Explain X');
    expect(entries[0].exchange).toEqual([{ who: 'Student', text: 'because Y' }]);
  });

  it('has no score anywhere while the grade is unreleased', () => {
    const { meta, entries } = resultsTranscript(RESULT);
    expect(meta.score).toBeNull();
    expect(entries.every((e) => e.score === null)).toBe(true);
  });

  it('includes released scores and evaluator feedback', () => {
    const { meta, entries } = resultsTranscript({
      ...RESULT, score: 84, grade_released: true,
      question_results: [{ question_id: 'q1', question_text: 'Explain X', answer: 'because Y', score: 80, feedback: 'Good' }],
    });
    expect(meta.score).toBe(84);
    expect(entries[0].score).toBe(80);
    expect(entries[0].exchange).toEqual([
      { who: 'Student', text: 'because Y' },
      { who: 'Evaluator', text: 'Good' },
    ]);
  });
});

describe('examTranscript', () => {
  const questions = [{ topic: 'Axons', text: 'Explain X' }];
  const qData = [{
    attempted: true, score: 0.8,
    turns: [
      { role: 'evaluator', text: 'Explain X' },
      { role: 'student', text: 'because Y' },
      { role: 'evaluator', text: 'Good — now justify it.' },
    ],
  }];
  const opts = { overallScore: 72, answered: 1 };

  it('matches Results on a graded item: answers only, no EDS, no evaluator', () => {
    const { meta, entries } = examTranscript(questions, qData, { ...opts, showDraftScores: false });
    expect(meta.score).toBeNull();
    expect(entries[0].score).toBeNull();
    expect(entries[0].exchange).toEqual([{ who: 'Student', text: 'because Y' }]);
  });

  it('keeps the EDS and the evaluator for practice (and professor preview)', () => {
    const { meta, entries } = examTranscript(questions, qData, { ...opts, showDraftScores: true });
    expect(meta.score).toBe(72);
    expect(entries[0].score).toBe(80);           // 0..1 in the exam, 0-100 in the transcript
    expect(entries[0].exchange.map((e) => e.who)).toEqual(['Student', 'Evaluator']);
  });

  it('withheld transcript prints no EDS text at all', () => {
    const { meta, entries } = examTranscript(questions, qData, { ...opts, showDraftScores: false });
    downloadTranscriptPdf(meta, entries);
    expect(drawn.some((t) => /EDS/.test(t))).toBe(false);
    expect(drawn).toContain('because Y');
    expect(drawn.some((t) => /justify/.test(t))).toBe(false);
  });
});

describe('downloadTranscriptPdf', () => {
  it('omits every EDS line when nothing is scored', () => {
    const { meta, entries } = resultsTranscript(RESULT);
    downloadTranscriptPdf(meta, entries);
    expect(drawn.some((t) => /EDS/.test(t))).toBe(false);
    expect(drawn).toContain('because Y');            // the answers still ship
    expect(drawn).toContain('Questions Answered: 2 of 5');
    expect(save).toHaveBeenCalledWith(expect.stringMatching(/^Epistemy_Transcript_\d{4}-\d{2}-\d{2}\.pdf$/));
  });

  it('prints the overall and per-question EDS when scored', () => {
    downloadTranscriptPdf(
      { heading: 'Epistemy — Exam Transcript', totalQuestions: 2, answered: 2, score: 84 },
      [{ n: 1, topic: 'Axons', question: 'Explain X', attempted: true, score: 80, exchange: [] }],
    );
    expect(drawn).toContain('Overall EDS Score: 84');
    expect(drawn).toContain('Score: +80 EDS  •  Answered');
    expect(drawn).toContain('Question 1 — Axons');
  });
});
