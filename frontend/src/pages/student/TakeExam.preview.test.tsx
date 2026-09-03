import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Mock the exam API — EXACTLY the runtime symbols TakeExam imports from ../../api/exam.
vi.mock('../../api/exam', () => ({
  startExamSession: vi.fn().mockResolvedValue({
    session_id: 's1',
    questions: [{ question_id: 'q1', topic: 't', text: 'Q1?' }],
  }),
  submitAnswer: vi.fn(),
  getSessionStatus: vi.fn().mockResolvedValue({
    session_id: 's1', status: 'active', current_turn: 0, total_questions: 1, eds_score: 0, turns: [],
  }),
  completeSession: vi.fn().mockResolvedValue(undefined),
  getAssignmentCase: vi.fn().mockResolvedValue([]),
  publishAssignment: vi.fn().mockResolvedValue({ status: 'active', assignment_id: 'a1' }),
  discardDraft: vi.fn().mockResolvedValue({ status: 'discarded', assignment_id: 'a1' }),
}));

// TakeExam builds its own metadata via get() from ../../api/client — stub it so no network happens.
vi.mock('../../api/client', () => ({ get: vi.fn().mockResolvedValue({}) }));

import TakeExam from './TakeExam';

function renderPreview() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TakeExam assignmentId="a1" preview onExit={() => {}} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// Advance from the readiness screen into the taking phase (banner lives there).
async function enterTaking() {
  const startBtn = await screen.findByRole('button', { name: /start exam/i });
  fireEvent.click(startBtn);
}

describe('TakeExam preview mode', () => {
  beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });

  it('never writes exam progress to localStorage', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    renderPreview();
    await enterTaking();
    await waitFor(() =>
      expect(screen.getByText(/nothing is saved for students/i)).toBeInTheDocument()
    );
    const examWrites = setItem.mock.calls.filter(([k]) => String(k).startsWith('epistemy_exam_'));
    expect(examWrites).toHaveLength(0);
  });

  it('shows the preview banner', async () => {
    renderPreview();
    await enterTaking();
    await waitFor(() =>
      expect(screen.getByText(/nothing is saved for students/i)).toBeInTheDocument()
    );
  });
});
