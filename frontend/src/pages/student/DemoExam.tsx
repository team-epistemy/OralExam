import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import TakeExam from './TakeExam';
import { demoMeta, type DemoMeta } from '../../api/demo';

// Public, credential-free demo. Validates the token (expiry / attempt cap live on the
// server), then hands off to the normal exam UI in practice mode via a demoToken — all
// its API calls route to the public /api/demo/<token>/* endpoints.
export default function DemoExam() {
  const { token } = useParams<{ token: string }>();
  const [meta, setMeta] = useState<DemoMeta | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) { setError('This demo link is missing its token.'); return; }
    let cancelled = false;
    demoMeta(token)
      .then((dm) => {
        if (cancelled) return;
        if (dm.status && dm.status !== 'ok') setError(dm.message || 'This demo link is not available.');
        else setMeta(dm);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : 'This demo link is not available.'); });
    return () => { cancelled = true; };
  }, [token]);

  if (error) {
    return (
      <div className="min-h-[80vh] flex items-center justify-center px-4">
        <div className="max-w-md text-center">
          <div className="w-12 h-12 bg-amber-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <span className="text-amber-600 text-xl font-bold">!</span>
          </div>
          <h1 className="text-lg font-semibold text-ink mb-2">Demo unavailable</h1>
          <p className="text-sm text-muted">{error}</p>
        </div>
      </div>
    );
  }
  if (!meta) {
    return (
      <div className="min-h-[80vh] flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-gold animate-spin" />
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      <div className="mb-3 flex flex-wrap items-center gap-2 bg-parchment-dark border border-border text-navy rounded-xl px-4 py-2.5 text-sm">
        <span className="font-semibold">Epistemy demo</span>
        <span>— no login required. This is a practice run; nothing is saved.</span>
        <span className="ml-auto text-gold text-xs">{meta.attempts_remaining} attempt{meta.attempts_remaining === 1 ? '' : 's'} left</span>
      </div>
      <TakeExam demoToken={token} assignmentId={meta.assignment_id} />
    </div>
  );
}
