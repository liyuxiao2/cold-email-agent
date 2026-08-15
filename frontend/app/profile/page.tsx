'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertTriangle, ArrowLeft, FileText } from 'lucide-react';
import { getProfile, saveProfile, startGoogleLogin, type SenderProfile } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import ProfileForm from '@/components/ProfileForm';
import ResumeUpload from '@/components/ResumeUpload';
import LlmKeyForm from '@/components/LlmKeyForm';

const cardStyle: React.CSSProperties = {
  backgroundColor: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
  borderRadius: '16px',
  padding: '2rem',
  marginBottom: '1.5rem',
};

/**
 * Edit an existing profile. Reachable once onboarding has produced a complete
 * profile (app/page.tsx's gate sends incomplete profiles to /onboarding
 * instead), but GET /api/profile can still 404 — e.g. hitting this URL
 * directly right after sign-in — so that case is handled the same way
 * onboarding handles "no résumé yet": an empty form.
 */
export default function ProfilePage() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();

  const [profile, setProfile] = useState<SenderProfile | null>(null);
  const [resumeSuggestion, setResumeSuggestion] = useState<Partial<SenderProfile> | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);

  useEffect(() => {
    if (!authLoading && !user) router.push('/login');
  }, [authLoading, user, router]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setProfile(await getProfile());
    } catch {
      // No profile yet (404) — fall through to an empty form rather than
      // stranding the user on an error page.
      setProfile(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user) load();
  }, [user, load]);

  if (authLoading || !user || loading) return null;

  const handleSave = async (updated: Partial<SenderProfile>) => {
    const saved = await saveProfile(updated);
    setProfile(saved);
    setResumeSuggestion(null);
    setNotice('Profile saved.');
    setTimeout(() => setNotice(null), 3000);
  };

  const handleReconnectGmail = async () => {
    setReconnecting(true);
    try {
      await startGoogleLogin();
    } catch {
      setReconnecting(false);
    }
  };

  // The résumé upload only returns a SUGGESTION; it never overwrites the
  // saved profile by itself. Feeding it into ProfileForm's `initial` lets the
  // user review/edit the extraction before it becomes a PUT.
  const formInitial: Partial<SenderProfile> = resumeSuggestion ?? profile ?? {};

  return (
    <main style={{ maxWidth: '640px', margin: '0 auto', padding: '3rem 1.5rem' }}>
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

      <h1 style={{ fontSize: '1.5rem', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: '1.5rem' }}>
        Your profile
      </h1>

      {notice && (
        <p
          style={{
            fontSize: '0.85rem',
            color: 'var(--success)',
            backgroundColor: 'var(--success-bg)',
            border: '1px solid var(--success)',
            borderRadius: '8px',
            padding: '0.6rem 1rem',
            marginBottom: '1.5rem',
          }}
        >
          {notice}
        </p>
      )}

      {!user.gmail_connected && (
        <div
          style={{
            ...cardStyle,
            border: '1px solid var(--warning)',
            backgroundColor: 'var(--warning-bg)',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '12px',
          }}
        >
          <AlertTriangle size={20} color="var(--warning)" style={{ flexShrink: 0, marginTop: '2px' }} />
          <div>
            <p style={{ fontWeight: 700, marginBottom: '0.25rem' }}>Gmail isn&apos;t connected</p>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
              Drafts can&apos;t be created for you until Gmail is connected. This can happen if
              Google didn&apos;t hand back a refresh token on your last sign-in — reconnecting
              forces a fresh consent screen so one is issued.
            </p>
            <button
              onClick={handleReconnectGmail}
              disabled={reconnecting}
              style={{
                padding: '10px 18px',
                borderRadius: '10px',
                backgroundColor: 'var(--accent-primary)',
                color: '#fff',
                border: 'none',
                fontWeight: 700,
                fontSize: '0.85rem',
                cursor: reconnecting ? 'not-allowed' : 'pointer',
              }}
            >
              {reconnecting ? 'Redirecting…' : 'Reconnect Gmail'}
            </button>
          </div>
        </div>
      )}

      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '1rem' }}>
          <FileText size={18} color="var(--text-secondary)" />
          <span style={{ fontWeight: 700 }}>
            {profile?.has_resume ? `Résumé on file: ${profile.resume_filename}` : 'No résumé on file'}
          </span>
        </div>
        <ResumeUpload onSuggestion={setResumeSuggestion} label="Replace résumé (PDF, under 5MB)" />
        {resumeSuggestion !== null && (
          <p
            style={{
              marginTop: '1rem',
              fontSize: '0.8rem',
              color: 'var(--text-secondary)',
              backgroundColor: 'var(--info-bg)',
              border: '1px solid var(--info)',
              borderRadius: '8px',
              padding: '0.75rem 1rem',
            }}
          >
            The résumé was stored. The form below is now prefilled with a fresh
            suggestion from it — review and save to apply it.
          </p>
        )}
      </div>

      <div style={cardStyle}>
        <ProfileForm key={resumeSuggestion ? 'suggested' : 'saved'} initial={formInitial} onSave={handleSave} />
      </div>

      <div style={cardStyle}>
        <LlmKeyForm />
      </div>
    </main>
  );
}
