'use client';

import React, { useState } from 'react';
import { FileUp, Loader2 } from 'lucide-react';
import { uploadResume, type SenderProfile } from '@/lib/api';

const MAX_BYTES = 5 * 1024 * 1024;

interface ResumeUploadProps {
  /** Called with the SUGGESTED profile extracted from the résumé. Nothing is
   * saved by this component — the caller decides when to PUT /api/profile. */
  onSuggestion: (suggestion: Partial<SenderProfile>) => void;
  label?: string;
}

/**
 * Presentational: a résumé file picker with a client-side size/type pre-check.
 *
 * The pre-check below is a COURTESY only — it saves the user a ~5MB round
 * trip for an upload that's obviously going to be rejected. It is trivially
 * bypassable (this is client JS) and is NOT the enforcement boundary; the
 * server's `validate_resume` (magic-byte + size check) is the actual gate.
 */
export default function ResumeUpload({ onSuggestion, label }: ResumeUploadProps) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const handle = async (file: File) => {
    if (file.size > MAX_BYTES) {
      setError('Please upload a PDF under 5MB.');
      return;
    }
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError('Please upload a PDF.');
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const result = await uploadResume(file);
      onSuggestion(result.suggested);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <label
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '0.75rem',
          padding: '2rem 1.5rem',
          borderRadius: '12px',
          border: '1px dashed var(--border-color)',
          backgroundColor: 'var(--bg-secondary)',
          cursor: busy ? 'not-allowed' : 'pointer',
          textAlign: 'center',
        }}
      >
        <input
          type="file"
          accept="application/pdf"
          disabled={busy}
          onChange={(e) => e.target.files?.[0] && handle(e.target.files[0])}
          style={{ display: 'none' }}
        />
        {busy ? (
          <Loader2 size={22} color="var(--accent-primary)" style={{ animation: 'spin 1s linear infinite' }} />
        ) : (
          <FileUp size={22} color="var(--text-secondary)" />
        )}
        <span style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text-primary)' }}>
          {busy ? 'Reading your résumé…' : (label ?? 'Upload résumé (PDF, under 5MB)')}
        </span>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          We&apos;ll suggest a profile from it — you review and confirm before anything is saved.
        </span>
      </label>
      {error && (
        <p style={{ marginTop: '0.5rem', fontSize: '0.8rem', color: 'var(--danger)' }}>{error}</p>
      )}
    </div>
  );
}
