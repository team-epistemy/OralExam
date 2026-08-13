// Base URL for the backend API. Empty string = same origin (deployed together).
// Override via VITE_API_URL for local dev against remote backend.
export const API_BASE_URL = import.meta.env.VITE_API_URL || '';

// Default organization name sent in request headers when none is specified.
export const DEFAULT_ORG = 'epistemy';
