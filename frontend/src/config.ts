// Base URL for the backend API. Empty string = same origin (deployed together).
// Override via VITE_API_URL for local dev against remote backend.
export const API_BASE_URL = import.meta.env.VITE_API_URL || '';

// Default organization name sent in request headers when none is specified.
export const DEFAULT_ORG = 'epistemy';

// In-app path → URL under the SPA's base (production serves the app at /app/).
// Use for HARD navigations / shareable links; react-router's navigate()/<Link>
// already apply the basename. A bare "/professor/..." outside the base hits the
// backend instead of the SPA and fails its bearer-token auth with a 401.
export function appPath(path: string): string {
  const base = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}
