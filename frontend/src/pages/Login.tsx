import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { beginLogin } from '../api/auth';

// Password auth is gone: sign-in delegates to the Cognito Hosted UI (PKCE).
export default function Login() {
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSignIn = async () => {
    setError('');
    setLoading(true);
    try {
      await beginLogin(); // redirects to the Hosted UI; no return on success
    } catch {
      setError('Unable to reach the sign-in service. Please try again.');
      setLoading(false);
    }
  };

  return (
    <div>
      <h2 className="text-lg font-semibold text-ink mb-1">Welcome back</h2>
      <p className="text-sm text-muted mb-6">Sign in to continue</p>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      <button
        onClick={handleSignIn}
        disabled={loading}
        className="w-full flex items-center justify-center gap-2 py-2.5 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {loading && <Loader2 className="w-4 h-4 animate-spin" />}
        Sign In
      </button>
    </div>
  );
}
