import { API_BASE_URL, DEFAULT_ORG } from '../config';
import { refreshAccessToken } from './auth';

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = 'ApiError';
  }
}

function getAuthHeaders(): Record<string, string> {
  // Identity comes from the bearer token alone. The server overwrites any
  // x-role / x-user-id / x-org-name it receives with the token's claims, so
  // sending them here would be decorative at best and misleading at worst.
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'x-org-name': DEFAULT_ORG,
  };
  const token = localStorage.getItem('token');
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    if (response.status === 401) {
      // Reached here only after a silent refresh was impossible or also failed.
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      localStorage.removeItem('refresh_token');
      // Only redirect to login if we're not already on the login page
      if (!window.location.pathname.endsWith('/login')) {
        window.location.href = '/login';
      }
    }
    const errorBody = await response.text();
    // FastAPI returns {"detail": "..."} — surface that human message rather than
    // the raw JSON blob, so callers can show it directly (e.g. upload limits).
    let message = errorBody || response.statusText;
    try {
      const parsed = JSON.parse(errorBody);
      if (parsed && typeof parsed.detail === 'string') message = parsed.detail;
    } catch { /* not JSON — keep the raw text */ }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json();
}

// All JSON calls flow through here so a 401 can trigger one silent token refresh
// and a transparent replay before surfacing the error (which redirects to login).
async function request<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: getAuthHeaders(),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (response.status === 401 && !retried) {
    const refreshed = await refreshAccessToken();
    if (refreshed) return request<T>(method, path, body, true);
  }
  return handleResponse<T>(response);
}

export function get<T>(path: string): Promise<T> {
  return request<T>('GET', path);
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('POST', path, body);
}

export function put<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('PUT', path, body);
}

export function del<T>(path: string): Promise<T> {
  return request<T>('DELETE', path);
}

export async function putFile(url: string, file: File): Promise<void> {
  await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': file.type },
    body: file,
  });
}

export function createSSEConnection(path: string): EventSource {
  const token = localStorage.getItem('token');
  const url = `${API_BASE_URL}${path}${path.includes('?') ? '&' : '?'}token=${token}`;
  return new EventSource(url);
}

export { ApiError };
