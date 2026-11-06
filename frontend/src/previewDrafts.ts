// Tracks the throwaway Draft assignments that "Preview as Student" creates, so
// they get discarded even when the professor leaves the preview without its own
// exit buttons (browser back, refresh, closing the tab).
//
// Entries live in localStorage (survives refresh and tab close). Each live
// preview refreshes its entry's timestamp on a heartbeat; the sweep only removes
// entries whose heartbeat has stopped, so a preview still open in ANOTHER tab is
// never discarded out from under it.
import { API_BASE_URL, DEFAULT_ORG } from './config';

const KEY = 'epistemy_preview_drafts';
export const HEARTBEAT_MS = 30_000;
export const STALE_MS = 2 * 60_000;

type Entry = { id: string; ts: number };

function read(): Entry[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(v) ? v.filter((e) => e && typeof e.id === 'string' && typeof e.ts === 'number') : [];
  } catch {
    return [];
  }
}

function write(entries: Entry[]) {
  if (entries.length) localStorage.setItem(KEY, JSON.stringify(entries));
  else localStorage.removeItem(KEY);
}

/** Record (or refresh the heartbeat of) a live preview draft. */
export function trackPreviewDraft(id: string, now = Date.now()) {
  write([...read().filter((e) => e.id !== id), { id, ts: now }]);
}

export function untrackPreviewDraft(id: string) {
  write(read().filter((e) => e.id !== id));
}

/** Ids of preview drafts whose preview is no longer alive anywhere. */
export function stalePreviewDrafts(now = Date.now()): string[] {
  return read().filter((e) => now - e.ts > STALE_MS).map((e) => e.id);
}

/**
 * Discard every abandoned preview draft. `keep` is a draft the professor is
 * deliberately editing (e.g. opened via "Continue editing") — it's adopted, not
 * deleted. Entries are dropped once the discard succeeds or the server says the
 * draft is gone / no longer a draft (published); transient failures retry later.
 */
export async function sweepPreviewDrafts(
  discard: (id: string) => Promise<unknown>,
  keep?: string,
  now = Date.now(),
) {
  if (keep) untrackPreviewDraft(keep);
  for (const id of stalePreviewDrafts(now)) {
    if (id === keep) continue;
    try {
      await discard(id);
      untrackPreviewDraft(id);
    } catch (e) {
      const status = (e as { status?: number })?.status;
      if (status === 403 || status === 404) untrackPreviewDraft(id);
    }
  }
}

/**
 * Best-effort discard while the page is unloading (refresh / tab close). Uses a
 * keepalive fetch so the request outlives the page; if it doesn't land, the
 * entry stays tracked and the next sweep removes the draft.
 */
export function discardOnUnload(id: string) {
  const token = localStorage.getItem('token');
  try {
    fetch(`${API_BASE_URL}/api/assignments/${id}/discard`, {
      method: 'POST',
      keepalive: true,
      headers: {
        'Content-Type': 'application/json',
        'x-org-name': DEFAULT_ORG,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    }).then((r) => { if (r.ok || r.status === 403) untrackPreviewDraft(id); }).catch(() => {});
  } catch { /* unsupported — the sweep covers it */ }
}
