'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Zap } from 'lucide-react';
import { saveProfile, type SenderProfile } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import ResumeUpload from '@/components/ResumeUpload';
import ProfileForm from '@/components/ProfileForm';

/**
 * First-run flow for a signed-in user with no complete profile yet
 * (app/page.tsx redirects here when `user.profile_complete` is false).
 *
 * Upload is optional context, not a requirement: `ResumeUpload` only ever
 * hands back a SUGGESTED profile for review, and "Skip and fill in manually"
 * exists so a user whose PDF is a scan (422 from the server) is never stuck
 * with no way to proceed.
 */
export default function OnboardingPage() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();
  const [suggestion, setSuggestion] = useState<Partial<SenderProfile> | null>(null);
  const [manual, setManual] = useState(false);

  useEffect(() => {
    if (authLoading) return;
    if (!user) router.push('/login');
    else if (user.profile_complete) router.push('/');
  }, [authLoading, user, router]);

  if (authLoading || !user || user.profile_complete) return null;

  const showForm = suggestion !== null || manual;

  const handleSave = async (profile: Partial<SenderProfile>) => {
    await saveProfile(profile);
    router.push('/');
  };

  return (
    <main style={{ maxWidth: '640px', margin: '0 auto', padding: '3rem 1.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '0.5rem' }}>
        <div
          style={{
            width: '36px',
            height: '36px',
            borderRadius: '10px',
            background: 'var(--accent-gradient)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Zap size={20} color="#fff" />
        </div>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 800, letterSpacing: '-0.02em' }}>
          Set up your sender profile
        </h1>
      </div>
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '2rem' }}>
        This is what shows up in the emails sent on your behalf: your intro and the
        experience bullets the model can draw from.
      </p>

      {!showForm && (
        <div
          style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            borderRadius: '16px',
            padding: '2rem',
          }}
        >
          <ResumeUpload onSuggestion={setSuggestion} />
          <div style={{ textAlign: 'center', marginTop: '1.25rem' }}>
            <button
              type="button"
              onClick={() => setManual(true)}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-secondary)',
                fontSize: '0.85rem',
                textDecoration: 'underline',
                cursor: 'pointer',
              }}
            >
              Skip and fill in manually
            </button>
          </div>
        </div>
      )}

      {showForm && (
        <div
          style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            borderRadius: '16px',
            padding: '2rem',
          }}
        >
          {suggestion !== null && (
            <p
              style={{
                fontSize: '0.8rem',
                color: 'var(--text-secondary)',
                backgroundColor: 'var(--info-bg)',
                border: '1px solid var(--info)',
                borderRadius: '8px',
                padding: '0.75rem 1rem',
                marginBottom: '1.25rem',
              }}
            >
              This is a suggestion extracted from your résumé — nothing is saved yet.
              Review and edit it, then save.
            </p>
          )}
          <ProfileForm initial={suggestion ?? {}} onSave={handleSave} saveLabel="Save and continue" />
        </div>
      )}
    </main>
  );
}
