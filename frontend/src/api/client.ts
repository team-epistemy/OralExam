import { API_BASE_URL, DEFAULT_ORG } from '../config';

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
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      // Only redirect to login if we're not already on the login page
      if (!window.location.pathname.endsWith('/login')) {
        window.location.href = '/login';
      }
    }
    const errorBody = await response.text();
    throw new ApiError(errorBody || response.statusText, response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json();
}

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'GET',
    headers: getAuthHeaders(),
  });
  return handleResponse<T>(response);
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  });
  return handleResponse<T>(response);
}

export async function put<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'PUT',
    headers: getAuthHeaders(),
    body: body ? JSON.stringify(body) : undefined,
  });
  return handleResponse<T>(response);
}

export async function del<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'DELETE',
    headers: getAuthHeaders(),
  });
  return handleResponse<T>(response);
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
