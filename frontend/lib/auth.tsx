'use client';

/** Client-side auth state. */

import { createContext, useContext, useEffect, useState } from 'react';

export type User = {
  id: string;
  email: string;
  name: string | null;
  picture_url: string | null;
  role: 'user' | 'admin';
  gmail_connected: boolean;
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
