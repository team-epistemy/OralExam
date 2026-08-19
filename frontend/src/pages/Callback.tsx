import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { completeLogin } from '../api/auth';
import { get } from '../api/client';

interface Me {
  id: string;
  email: string;
  role: 'professor' | 'student' | 'platform_admin';
  orgId: string;
}

// Cognito redirects here with ?code&state. Exchange for a token, load the
// verified identity, then route by role.
export default function Callback() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [error, setError] = useState('');

  useEffect(() => {
    const code = params.get('code');
    const state = params.get('state');
    if (!code || !state) {
      setError('Missing authorization code.');
      return;
    }
    (async () => {
      try {
        const token = await completeLogin(code, state);
        localStorage.setItem('token', token);
        const me = await get<Me>('/api/auth/me');
        localStorage.setItem('user', JSON.stringify(me));
        if (me.role === 'platform_admin') navigate('/admin/professors', { replace: true });
        else if (me.role === 'professor') navigate('/professor/dashboard', { replace: true });
        else if (me.role === 'student') navigate('/student/dashboard', { replace: true });
        else navigate('/', { replace: true });
      } catch {
        localStorage.removeItem('token');
        setError('Sign-in failed. Please try again.');
      }
    })();
  }, [params, navigate]);

  if (error) {
    return (
      <div>
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
        <button
          onClick={() => navigate('/login', { replace: true })}
          className="w-full py-2.5 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light"
        >
          Back to sign in
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted">
      <Loader2 className="w-4 h-4 animate-spin" /> Signing you in…
    </div>
  );
}
