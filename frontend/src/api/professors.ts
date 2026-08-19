import { post } from './client';

export interface Professor {
  id: string;
  email: string;
  role: string;
}

// platform_admin creates a professor in their org (Cognito user + app_user row).
// Omitting password leaves the account without one until set out of band.
export function createProfessor(email: string, password?: string): Promise<Professor> {
  return post<Professor>('/api/auth/professors', { email, password: password || null });
}
