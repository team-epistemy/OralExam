import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../../api/exam', () => ({
  startExamSession: vi.fn().mockResolvedValue({
    session_id: 's1',
    questions: [{ question_id: 'q1', topic: 'Axons', text: 'Q1?' }],
  }),
  submitAnswer: vi.fn(),
  getSessionStatus: vi.fn().mockResolvedValue({
    session_id: 's1', status: 'active', current_turn: 0, total_questions: 1, eds_score: 0, turns: [],
  }),
  completeSession: vi.fn().mockResolvedValue(undefined),
  getAssignmentCase: vi.fn().mockResolvedValue([]),
  publishAssignment: vi.fn(),
  discardDraft: vi.fn(),
}));

const get = vi.fn();
vi.mock('../../api/client', () => ({ get: (...args: unknown[]) => get(...args) }));

import TakeExam from './TakeExam';

function renderAs(assignmentType: string) {
  get.mockResolvedValue({ assignment_type: assignmentType, config: { max_questions: 1 } });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TakeExam assignmentId="a1" />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

// Take the assignment through to the taking phase, where the sidebar lives.
async function takeAs(assignmentType: string) {
  renderAs(assignmentType);
  fireEvent.click(await screen.findByRole('button', { name: /^start /i }));
  await waitFor(() => expect(screen.getByPlaceholderText(/answer the question above/i)).toBeInTheDocument());
}

describe('in-exam EDS and concept map', () => {
  beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });

  it('are hidden on an assignment — the professor releases the grade', async () => {
    await takeAs('assignment');
    expect(screen.queryByText(/Epistemic Depth Score/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Concept Map/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Progress/i)).toBeInTheDocument();   // sidebar still useful
  });

  it('are hidden on an exam too', async () => {
    await takeAs('exam');
    expect(screen.queryByText(/Epistemic Depth Score/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Concept Map/i)).not.toBeInTheDocument();
  });

  it('stay visible on a practice test — nothing there is graded', async () => {
    await takeAs('practice');
    // The heading plus the explainer both name it, hence getAllByText.
    expect(screen.getAllByText(/Epistemic Depth Score/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Concept Map/i)).toBeInTheDocument();
  });
});

describe('button labels name the item type', () => {
  beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });

  it('a practice test starts and submits as a practice test, never an exam', async () => {
    renderAs('practice');
    expect(await screen.findByRole('button', { name: /start practice test/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /start exam/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /start practice test/i }));
    await waitFor(() => expect(screen.getByPlaceholderText(/answer the question above/i)).toBeInTheDocument());
    expect(screen.getAllByRole('button', { name: /submit practice test/i }).length).toBeGreaterThan(0);
  });

  it('an assignment starts and submits as an assignment', async () => {
    renderAs('assignment');
    expect(await screen.findByRole('button', { name: /start assignment/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /start assignment/i }));
    await waitFor(() => expect(screen.getByPlaceholderText(/answer the question above/i)).toBeInTheDocument());
    expect(screen.getAllByRole('button', { name: /submit assignment/i }).length).toBeGreaterThan(0);
  });

  it('an exam still says Exam', async () => {
    renderAs('exam');
    expect(await screen.findByRole('button', { name: /start exam/i })).toBeInTheDocument();
  });
});
