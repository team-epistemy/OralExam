import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
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

function renderPreview(onExit: () => void = () => {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/professor/assignments/new']}>
        <Routes>
          <Route path="/professor/assignments/new" element={<TakeExam assignmentId="a1" preview onExit={onExit} />} />
          <Route path="/student/dashboard" element={<div>STUDENT DASHBOARD</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// Advance from the readiness screen into the taking phase (banner lives there).
async function enterTaking() {
  // The Start button is named after the item type ('Start Assignment', etc).
  const startBtn = await screen.findByRole('button', { name: /^start /i });
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

  // A professor's dry-run must never route into the student area: for a
  // professor, /student/* bounces to /login and strands the draft.
  it('Back on the readiness screen exits the preview instead of going to the student dashboard', async () => {
    const onExit = vi.fn();
    renderPreview(onExit);
    fireEvent.click(await screen.findByRole('button', { name: /back to dashboard/i }));
    expect(onExit).toHaveBeenCalled();
    expect(screen.queryByText('STUDENT DASHBOARD')).not.toBeInTheDocument();
  });

  it('Back in the taking phase exits the preview instead of going to the student dashboard', async () => {
    const onExit = vi.fn();
    renderPreview(onExit);
    await enterTaking();
    await screen.findByText(/nothing is saved for students/i);
    fireEvent.click(screen.getByRole('button', { name: /^back$/i }));
    expect(onExit).toHaveBeenCalled();
    expect(screen.queryByText('STUDENT DASHBOARD')).not.toBeInTheDocument();
  });
});
