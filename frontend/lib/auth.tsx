'use client';

/**
 * Client-side auth state.
 *
 * The session cookie is httpOnly, so JavaScript cannot read it. Identity is
 * therefore never derived from the cookie — it comes exclusively from
 * `GET /api/auth/me`, which the browser answers by replaying the cookie.
 */

import { createContext, useContext, useEffect, useState } from 'react';

export type User = {
  id: string;
  email: string;
  name: string | null;
  picture_url: string | null;
  role: 'user' | 'admin';
  gmail_connected: boolean;
  // Drives the onboarding gate in app/page.tsx. Comes straight from
  // GET /api/auth/me, which computes it server-side (name + intro present).
  profile_complete: boolean;
};

type AuthState = { user: User | null; loading: boolean; logout: () => Promise<void> };

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  logout: async () => {},
});

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Deliberately not routed through lib/api.ts `request`: a 401 here is the
    // normal "not signed in yet" answer, not an error, and must not trigger the
    // redirect (the login page renders inside this provider too).
    fetch(`${API_URL}/api/auth/me`, { credentials: 'include', cache: 'no-store' })
      .then((r) => (r.ok ? (r.json() as Promise<User>) : null))
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const logout = async () => {
    await fetch(`${API_URL}/api/auth/logout`, { method: 'POST', credentials: 'include' });
    setUser(null);
    window.location.href = '/login';
  };

  return (
    <AuthContext.Provider value={{ user, loading, logout }}>{children}</AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
