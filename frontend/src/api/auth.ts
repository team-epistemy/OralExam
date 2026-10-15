// Cognito Hosted-UI authorization-code + PKCE flow for the public SPA client.
// No client secret: security rests on the code_verifier never leaving the browser.
import { API_BASE_URL } from '../config';

interface AuthConfig {
  provider?: 'cognito' | 'gcip';
  hostedUiUrl: string;
  clientId: string;
  scopes: string[];
  // GCIP (Identity Platform) mode:
  apiKey?: string;
  projectId?: string;
  authDomain?: string;
}

let _cfg: AuthConfig | null = null;

async function authConfig(): Promise<AuthConfig> {
  if (_cfg) return _cfg;
  const res = await fetch(`${API_BASE_URL}/api/auth/config`);
  if (!res.ok) throw new Error('auth config unavailable');
  _cfg = await res.json();
  return _cfg!;
}

// Which IdP the backend is configured for; drives the login UX at runtime so the
// same build works against a Cognito backend (Hosted UI) or a GCIP one (password).
export async function authProvider(): Promise<'cognito' | 'gcip'> {
  return (await authConfig()).provider ?? 'cognito';
}

// GCIP email/password sign-in via the Identity Platform REST API. On first
// sign-in the user may not exist in GCIP yet — fall back to the backend JIT
// migration (verifies against Cognito + provisions the GCIP user), then retry.
// Stores the ID token as the bearer token, like the Cognito path.
export async function gcipLogin(email: string, password: string): Promise<void> {
  const cfg = await authConfig();
  const signIn = () => fetch(
    `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${cfg.apiKey}`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, returnSecureToken: true }) });

  let res = await signIn();
  if (!res.ok) {
    const mig = await fetch(`${API_BASE_URL}/api/auth/migrate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }) });
    if (!mig.ok) throw new Error('Invalid email or password.');
    res = await signIn();
  }
  if (!res.ok) throw new Error('Invalid email or password.');
  const data = await res.json();
  localStorage.setItem('token', data.idToken);
  if (data.refreshToken) localStorage.setItem('refresh_token', data.refreshToken);
}

function base64url(bytes: Uint8Array): string {
  let s = '';
  bytes.forEach((b) => (s += String.fromCharCode(b)));
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function randomString(len = 48): string {
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  return base64url(bytes);
}

async function sha256(input: string): Promise<Uint8Array> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));
  return new Uint8Array(digest);
}

// Cognito must have this exact URL registered as a callback for the client.
function redirectUri(): string {
  return `${window.location.origin}${import.meta.env.BASE_URL}callback`;
}

export async function beginLogin(): Promise<void> {
  const cfg = await authConfig();
  const verifier = randomString();
  const state = randomString(16);
  sessionStorage.setItem('pkce_verifier', verifier);
  sessionStorage.setItem('pkce_state', state);
  const url = new URL(`${cfg.hostedUiUrl}/oauth2/authorize`);
  url.search = new URLSearchParams({
    response_type: 'code',
    client_id: cfg.clientId,
    redirect_uri: redirectUri(),
    scope: cfg.scopes.join(' '),
    state,
    code_challenge: base64url(await sha256(verifier)),
    code_challenge_method: 'S256',
  }).toString();
  window.location.href = url.toString();
}

// Full logout: clear local session AND the Cognito Hosted-UI session cookie,
// so the next login can be a different user. Falls back to a local clear.
export async function logout(): Promise<void> {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  localStorage.removeItem('refresh_token');
  const logoutUri = `${window.location.origin}${import.meta.env.BASE_URL}login`;
  try {
    const cfg = await authConfig();
    if (cfg.provider === 'gcip') {
      // No Hosted-UI session to clear; the local token clear above is enough.
      window.location.href = logoutUri;
      return;
    }
    const url = new URL(`${cfg.hostedUiUrl}/logout`);
    url.search = new URLSearchParams({
      client_id: cfg.clientId,
      logout_uri: logoutUri,
    }).toString();
    window.location.href = url.toString();
  } catch {
    window.location.href = logoutUri;
  }
}

// Exchange the authorization code for tokens; returns the access token.
export async function completeLogin(code: string, state: string): Promise<string> {
  const cfg = await authConfig();
  if (state !== sessionStorage.getItem('pkce_state')) throw new Error('state mismatch');
  const verifier = sessionStorage.getItem('pkce_verifier');
  if (!verifier) throw new Error('missing PKCE verifier');
  const res = await fetch(`${cfg.hostedUiUrl}/oauth2/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: cfg.clientId,
      code,
      redirect_uri: redirectUri(),
      code_verifier: verifier,
    }).toString(),
  });
  if (!res.ok) throw new Error('token exchange failed');
  const tokens = await res.json();
  sessionStorage.removeItem('pkce_verifier');
  sessionStorage.removeItem('pkce_state');
  // Keep the refresh token so the session can be renewed silently (see
  // refreshAccessToken) instead of bouncing the user to login every ~hour.
  if (tokens.refresh_token) localStorage.setItem('refresh_token', tokens.refresh_token);
  return tokens.access_token as string;
}

// Single-flight silent refresh: exchange the stored refresh token for a fresh
// access token. Concurrent callers share one in-flight request. Returns the new
// access token (also written to localStorage) or null if refresh isn't possible.
let _refreshPromise: Promise<string | null> | null = null;

export function refreshAccessToken(): Promise<string | null> {
  if (_refreshPromise) return _refreshPromise;
  _refreshPromise = (async () => {
    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) return null;
    try {
      const cfg = await authConfig();
      if (cfg.provider === 'gcip') {
        // GCIP refresh via the Secure Token endpoint; returns a fresh ID token.
        const r = await fetch(`https://securetoken.googleapis.com/v1/token?key=${cfg.apiKey}`, {
          method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: refreshToken }).toString(),
        });
        if (!r.ok) return null;
        const t = await r.json();
        const idTok = t.id_token as string | undefined;
        if (!idTok) return null;
        localStorage.setItem('token', idTok);
        if (t.refresh_token) localStorage.setItem('refresh_token', t.refresh_token);
        return idTok;
      }
      const res = await fetch(`${cfg.hostedUiUrl}/oauth2/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'refresh_token',
          client_id: cfg.clientId,
          refresh_token: refreshToken,
        }).toString(),
      });
      if (!res.ok) return null;
      const tokens = await res.json();
      const access = tokens.access_token as string | undefined;
      if (!access) return null;
      localStorage.setItem('token', access);
      return access;
    } catch {
      return null;
    } finally {
      _refreshPromise = null;
    }
  })();
  return _refreshPromise;
}
