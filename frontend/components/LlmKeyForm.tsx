'use client';

import React, { useEffect, useState } from 'react';
import { deleteLlmKey, getLlmKey, setLlmKey, type LlmKeyStatus } from '@/lib/api';

const inputStyle: React.CSSProperties = {
  padding: '10px 12px',
  borderRadius: '8px',
  backgroundColor: 'var(--bg-secondary)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-primary)',
  fontSize: '0.875rem',
  outline: 'none',
};

const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * Bring-your-own-key form, rendered on the profile page.
 *
 * A configured key bypasses BOTH the shared Redis token bucket and the
 * monthly draft quota (see cold_email/quota.py) — it's the user's own
 * provider limits being spent, not the platform's shared ones.
 */
export default function LlmKeyForm() {
  const [status, setStatus] = useState<LlmKeyStatus | null>(null);
  const [provider, setProvider] = useState<'groq' | 'gemini'>('groq');
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = async () => {
    try {
      setStatus(await getLlmKey());
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await setLlmKey(provider, apiKey);
      setApiKey('');
      setNotice('Key saved.');
      setTimeout(() => setNotice(null), 3000);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async () => {
    setRemoving(true);
    setError(null);
    try {
      await deleteLlmKey();
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div>
      <h3 style={{ fontSize: '1rem', fontWeight: 700, marginBottom: '0.4rem' }}>Your own LLM key (optional)</h3>
      <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
        Using your own key removes the monthly draft limit.
      </p>

      {status?.configured && (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-primary)', marginBottom: '1rem' }}>
          Configured: <strong>{status.provider}</strong> key ending in <code>{status.last4}</code>.{' '}
          <button
            onClick={handleRemove}
            disabled={removing}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--danger)',
              cursor: removing ? 'not-allowed' : 'pointer',
              fontSize: '0.85rem',
              textDecoration: 'underline',
              padding: 0,
            }}
          >
            {removing ? 'Removing…' : 'Remove'}
          </button>
        </p>
      )}

      <form onSubmit={handleSave} style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value as 'groq' | 'gemini')}
          style={{ ...inputStyle, width: '120px' }}
        >
          <option value="groq">Groq</option>
          <option value="gemini">Gemini</option>
        </select>
        <input
          type="password"
          placeholder={status?.configured ? 'Replace key' : 'API key'}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          style={{ ...inputStyle, flex: '1 1 200px' }}
          required
        />
        <button
          type="submit"
          disabled={saving || !apiKey}
          style={{
            padding: '10px 18px',
            borderRadius: '8px',
            backgroundColor: 'var(--accent-primary)',
            color: '#fff',
            border: 'none',
            fontWeight: 700,
            fontSize: '0.85rem',
            cursor: saving || !apiKey ? 'not-allowed' : 'pointer',
          }}
        >
          {saving ? 'Validating…' : 'Save key'}
        </button>
      </form>

      {error && <p style={{ color: 'var(--danger)', fontSize: '0.85rem', marginTop: '0.75rem' }}>{error}</p>}
      {notice && <p style={{ color: 'var(--success)', fontSize: '0.85rem', marginTop: '0.75rem' }}>{notice}</p>}
    </div>
  );
}
