import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { post } from '../api/client';
import { Loader2 } from 'lucide-react';

interface LoginResponse {
  token: string;
  user: {
    id: string;
    email: string;
    role: 'professor' | 'student';
    name: string;
  };
}

export default function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const data = await post<LoginResponse>('/api/auth/login', { email, password });
      localStorage.setItem('token', data.token);
      localStorage.setItem('user', JSON.stringify(data.user));

      if (data.user.role === 'professor') {
        navigate('/professor/dashboard');
      } else {
        navigate('/student/dashboard');
      }
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'message' in err) {
        // Try to parse the backend error detail from the response body
        const msg = (err as { message: string }).message;
        try {
          const parsed = JSON.parse(msg);
          setError(parsed.detail || 'Invalid email or password');
        } catch {
          setError(msg || 'Invalid email or password');
        }
      } else {
        setError('Unable to connect to the server. Please try again.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h2 className="text-lg font-semibold text-ink mb-1">Welcome back</h2>
      <p className="text-sm text-muted mb-6">Sign in to your account</p>

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="email" className="block text-sm font-medium text-ink-light mb-1">
            Email
          </label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full px-3 py-2 bg-parchment border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gold focus:border-transparent"
            placeholder="you@university.edu"
          />
        </div>

        <div>
          <label htmlFor="password" className="block text-sm font-medium text-ink-light mb-1">
            Password
          </label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            className="w-full px-3 py-2 bg-parchment border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gold focus:border-transparent"
            placeholder="Enter your password"
          />
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full flex items-center justify-center gap-2 py-2.5 bg-navy text-white rounded-lg text-sm font-medium hover:bg-navy-light disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading && <Loader2 className="w-4 h-4 animate-spin" />}
          Sign In
        </button>
      </form>

      <p className="mt-6 text-center text-sm text-muted">
        Don't have an account?{' '}
        <Link to="/signup" className="text-gold hover:text-gold-light font-medium">
          Sign up
        </Link>
      </p>
    </div>
  );
}
