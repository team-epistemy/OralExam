// Cognito Hosted-UI authorization-code + PKCE flow for the public SPA client.
// No client secret: security rests on the code_verifier never leaving the browser.
import { API_BASE_URL } from '../config';

interface AuthConfig {
  hostedUiUrl: string;
  clientId: string;
  scopes: string[];
}

let _cfg: AuthConfig | null = null;

async function authConfig(): Promise<AuthConfig> {
  if (_cfg) return _cfg;
  const res = await fetch(`${API_BASE_URL}/api/auth/config`);
  if (!res.ok) throw new Error('auth config unavailable');
  _cfg = await res.json();
  return _cfg!;
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
  const logoutUri = `${window.location.origin}${import.meta.env.BASE_URL}login`;
  try {
    const cfg = await authConfig();
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
  return tokens.access_token as string;
}
