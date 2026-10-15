import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { beginLogin, authProvider, gcipLogin } from '../api/auth';
import { get } from '../api/client';

interface Me { role: 'professor' | 'student' | 'platform_admin' }

function routeForRole(role: string): string {
  if (role === 'platform_admin') return '/admin/professors';
  if (role === 'professor') return '/professor/dashboard';
  if (role === 'student') return '/student/dashboard';
  return '/';
}

// Sign-in adapts to the backend's identity provider: Cognito → Hosted-UI (PKCE)
// redirect; GCIP → email/password for an ID token (with JIT migration on first
// use). The same build serves both — it's driven by /api/auth/config at runtime.
export default function Login() {
  const navigate = useNavigate();
  const [provider, setProvider] = useState<'cognito' | 'gcip' | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    authProvider().then(setProvider).catch(() => setProvider('cognito'));
  }, []);

  const handleHostedUi = async () => {
    setError('');
    setLoading(true);
    try {
      await beginLogin(); // redirects to the Hosted UI; no return on success
    } catch {
      setError('Unable to reach the sign-in service. Please try again.');
      setLoading(false);
    }
  };

  const handlePasswordLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await gcipLogin(email.trim(), password);
      const me = await get<Me>('/api/auth/me');
      localStorage.setItem('user', JSON.stringify(me));
      navigate(routeForRole(me.role), { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed.');
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

      {provider === null ? (
        <div className="flex items-center justify-center gap-2 py-4 text-sm text-muted">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </div>
      ) : provider === 'gcip' ? (
        <form onSubmit={handlePasswordLogin} className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-muted mb-1">Email</label>
            <input
              type="email" required value={email} autoComplete="username"
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-muted mb-1">Password</label>
            <input
              type="password" required value={password} autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-navy"
            />
          </div>
          <button
            type="submit" disabled={loading}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading && <Loader2 className="w-4 h-4 animate-spin" />}
            Sign In
          </button>
        </form>
      ) : (
        <button
          onClick={handleHostedUi}
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 py-2.5 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading && <Loader2 className="w-4 h-4 animate-spin" />}
          Sign In
        </button>
      )}
    </div>
  );
}
