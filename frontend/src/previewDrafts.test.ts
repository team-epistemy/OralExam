import { describe, it, expect, vi, beforeEach } from 'vitest';
import { trackPreviewDraft, untrackPreviewDraft, stalePreviewDrafts, sweepPreviewDrafts, STALE_MS } from './previewDrafts';

const T0 = 1_000_000;

describe('preview draft tracking', () => {
  beforeEach(() => localStorage.clear());

  it('a fresh heartbeat is not stale; an old one is', () => {
    trackPreviewDraft('live', T0);
    trackPreviewDraft('dead', T0 - STALE_MS - 1);
    expect(stalePreviewDrafts(T0)).toEqual(['dead']);
  });

  it('sweep discards only abandoned drafts and leaves a live preview (other tab) alone', async () => {
    trackPreviewDraft('live', T0);
    trackPreviewDraft('dead', T0 - STALE_MS - 1);
    const discard = vi.fn().mockResolvedValue({});
    await sweepPreviewDrafts(discard, undefined, T0);
    expect(discard).toHaveBeenCalledTimes(1);
    expect(discard).toHaveBeenCalledWith('dead');
    expect(stalePreviewDrafts(T0 + STALE_MS + 1)).toEqual(['live']);
  });

  it('never discards the draft being continued, and stops tracking it', async () => {
    trackPreviewDraft('adopted', T0 - STALE_MS - 1);
    const discard = vi.fn().mockResolvedValue({});
    await sweepPreviewDrafts(discard, 'adopted', T0);
    expect(discard).not.toHaveBeenCalled();
    expect(stalePreviewDrafts(T0)).toEqual([]);
  });

  it('drops entries the server says are gone/published (403), keeps transient failures for retry', async () => {
    trackPreviewDraft('published', T0 - STALE_MS - 1);
    trackPreviewDraft('flaky', T0 - STALE_MS - 1);
    const discard = vi.fn((id: string) =>
      Promise.reject(Object.assign(new Error('x'), { status: id === 'published' ? 403 : 500 })));
    await sweepPreviewDrafts(discard, undefined, T0);
    expect(stalePreviewDrafts(T0)).toEqual(['flaky']);
  });

  it('untrack removes an entry', () => {
    trackPreviewDraft('a', T0 - STALE_MS - 1);
    untrackPreviewDraft('a');
    expect(stalePreviewDrafts(T0)).toEqual([]);
  });
});
