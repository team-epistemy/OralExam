import { useState } from 'react';
import { UserPlus, Loader2, CheckCircle } from 'lucide-react';
import { createProfessor } from '../../api/professors';
import { ApiError } from '../../api/client';

// platform_admin form: create a professor by email in the admin's org.
export default function AddProfessor() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [added, setAdded] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setAdded('');
    setBusy(true);
    try {
      const p = await createProfessor(email.trim(), password.trim() || undefined);
      setAdded(p.email);
      setEmail('');
      setPassword('');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to add professor.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-xl">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 bg-navy rounded-lg flex items-center justify-center">
          <UserPlus className="w-5 h-5 text-gold" />
        </div>
        <div>
          <h1 className="font-heading text-2xl text-navy">Add Professor</h1>
          <p className="text-sm text-muted">Create a professor account in your organization.</p>
        </div>
      </div>

      {added && (
        <div className="mb-4 p-3 bg-success-bg border border-success/30 rounded-lg text-sm text-success flex items-center gap-2">
          <CheckCircle className="w-4 h-4 shrink-0" />
          <span>{added} added as professor. Share their password so they can sign in.</span>
        </div>
      )}
      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">{error}</div>
      )}

      <form onSubmit={submit} className="bg-white border border-border rounded-lg p-6 space-y-4">
        <div>
          <label className="block text-sm font-medium text-ink mb-1">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="prof@university.edu"
            className="w-full px-3 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy/20"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-ink mb-1">Temporary password</label>
          <input
            type="text"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Min 12 chars: upper, lower, number, symbol"
            className="w-full px-3 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy/20"
          />
          <p className="text-xs text-muted mt-1">
            Leave blank to create the account without a password (they must set one out of band).
          </p>
        </div>
        <button
          type="submit"
          disabled={busy || !email.trim()}
          className="inline-flex items-center gap-2 px-4 py-2 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <UserPlus className="w-4 h-4" />}
          Add professor
        </button>
      </form>
    </div>
  );
}
