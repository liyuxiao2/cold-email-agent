'use client';

import React, { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { useAuth } from '@/lib/auth';
import CompanyPool from '@/components/CompanyPool';

/**
 * The company pool route: browse the shared, researched company pool and
 * select which ones to draft outreach for (POST /api/outreach).
 *
 * Thin container — CompanyPool owns its own fetching, filters, selection,
 * and submission, since this route has exactly one consumer of that state.
 */
export default function PoolPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.push('/login');
  }, [loading, user, router]);

  if (loading || !user) return null;

  return (
    <div style={{ maxWidth: '1024px', margin: '0 auto', padding: '2rem 1.5rem' }}>
      <button
        onClick={() => router.push('/')}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          background: 'none',
          border: 'none',
          color: 'var(--text-secondary)',
          fontSize: '0.85rem',
          cursor: 'pointer',
          marginBottom: '1.5rem',
        }}
      >
        <ArrowLeft size={14} /> Back to dashboard
      </button>

      <h1 style={{ fontSize: '1.5rem', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '0.5rem' }}>
        Company pool
      </h1>
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '1.5rem' }}>
        Browse researched companies and select the ones you want drafted for you.
      </p>

      <CompanyPool />
    </div>
  );
}
