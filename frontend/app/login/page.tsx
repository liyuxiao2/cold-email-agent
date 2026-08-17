'use client';

import { Suspense, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { Zap } from 'lucide-react';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function LoginCard() {
  const error = useSearchParams().get('error');
  const [signingIn, setSigningIn] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const signIn = async () => {
    try {
      setSigningIn(true);
      setFailure(null);
      const res = await fetch(`${API_URL}/api/auth/google/login`, { credentials: 'include' });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const { authorize_url } = (await res.json()) as { authorize_url: string };
      window.location.href = authorize_url;
    } catch (err) {
      setFailure(err instanceof Error ? err.message : 'Could not reach the server');
      setSigningIn(false);
    }
  };

  return (
    <div
      style={{
        width: '100%',
        maxWidth: '400px',
        padding: '2.5rem 2rem',
        borderRadius: '16px',
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        boxShadow: 'var(--shadow-md)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: '1.25rem',
        textAlign: 'center',
      }}
    >
      <div
        style={{
          width: '44px',
          height: '44px',
          borderRadius: '12px',
          background: 'var(--accent-gradient)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Zap size={24} color="#fff" />
      </div>

      <h1 style={{ fontSize: '1.5rem', fontWeight: 800, letterSpacing: '-0.02em' }}>
        Cold Email Agent
      </h1>

      {error === 'oauth_failed' && (
        <p style={{ color: '#f87171', fontSize: '0.85rem' }}>
          Sign-in failed. Please try again.
        </p>
      )}
      {error === 'not_allowed' && (
        <p style={{ color: '#f87171', fontSize: '0.85rem' }}>
          This account isn&apos;t authorized for this instance.
        </p>
      )}
      {failure && (
        <p style={{ color: '#f87171', fontSize: '0.85rem' }}>Sign-in failed: {failure}</p>
      )}

      <button
        onClick={signIn}
        disabled={signingIn}
        style={{
          width: '100%',
          padding: '12px 20px',
          borderRadius: '10px',
          backgroundColor: 'var(--accent-primary)',
          color: '#fff',
          border: 'none',
          fontWeight: 700,
          fontSize: '0.925rem',
          cursor: signingIn ? 'not-allowed' : 'pointer',
        }}
      >
        {signingIn ? 'Redirecting…' : 'Sign in with Google'}
      </button>

      <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', lineHeight: 1.5 }}>
        We request Gmail access so drafts are created and sent from your own mailbox.
      </p>
    </div>
  );
}

export default function LoginPage() {
  return (
    <main
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '2rem 1.5rem',
      }}
    >
      <Suspense fallback={null}>
        <LoginCard />
      </Suspense>
    </main>
  );
}
