'use client';

import React, { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react';
import type { SenderProfile } from '@/lib/api';

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 12px',
  borderRadius: '8px',
  backgroundColor: 'var(--bg-secondary)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-primary)',
  fontSize: '0.875rem',
  outline: 'none',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: '0.8rem',
  fontWeight: 600,
  color: 'var(--text-secondary)',
  marginBottom: '0.4rem',
};

const fieldWrapStyle: React.CSSProperties = { marginBottom: '1.1rem' };

const iconButtonStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: '32px',
  height: '32px',
  borderRadius: '8px',
  backgroundColor: 'var(--bg-secondary)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-secondary)',
  cursor: 'pointer',
  flexShrink: 0,
};

/** The bullet's text before the FIRST ": " is what the server treats as the
 * link label — matches _bullet_md's str.partition(": ") on the backend. */
function bulletLabel(bullet: string): string | null {
  const idx = bullet.indexOf(': ');
  return idx === -1 ? null : bullet.slice(0, idx).trim();
}

interface ProfileFormProps {
  initial: Partial<SenderProfile>;
  onSave: (profile: Partial<SenderProfile>) => Promise<void>;
  saveLabel?: string;
}

/**
 * Presentational form for every profile field EXCEPT the résumé bytes
 * themselves (that's ResumeUpload's job). Used both for onboarding (initial
 * may be a résumé-derived suggestion or empty) and for the profile page
 * (initial comes from GET /api/profile).
 */
export default function ProfileForm({ initial, onSave, saveLabel }: ProfileFormProps) {
  const [name, setName] = useState(initial.name ?? '');
  const [intro, setIntro] = useState(initial.intro ?? '');
  const [linkedin, setLinkedin] = useState(initial.linkedin ?? '');
  const [github, setGithub] = useState(initial.github ?? '');
  const [website, setWebsite] = useState(initial.website ?? '');
  const [bullets, setBullets] = useState<string[]>(initial.experience_pool ?? []);
  const [companyLinks, setCompanyLinks] = useState<Record<string, string>>(
    initial.company_links ?? {}
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Bullets without a "Label: " prefix are exactly what the server 422s on;
  // catching it client-side gives an inline hint instead of a failed save.
  const invalidBullets = useMemo(
    () => bullets.map((b, i) => ({ i, b })).filter(({ b }) => b.trim() !== '' && !b.includes(': ')),
    [bullets]
  );
  const linkableLabels = useMemo(() => {
    const labels = new Set<string>();
    for (const b of bullets) {
      const label = bulletLabel(b);
      if (label) labels.add(label);
    }
    return Array.from(labels);
  }, [bullets]);

  const updateBullet = (i: number, value: string) =>
    setBullets((prev) => prev.map((b, idx) => (idx === i ? value : b)));
  const removeBullet = (i: number) => setBullets((prev) => prev.filter((_, idx) => idx !== i));
  const addBullet = () => setBullets((prev) => [...prev, '']);
  const moveBullet = (i: number, delta: number) =>
    setBullets((prev) => {
      const next = [...prev];
      const target = i + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[i], next[target]] = [next[target], next[i]];
      return next;
    });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (invalidBullets.length > 0) {
      setError('Every bullet needs a "Company: achievement" label before it can be saved.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const trimmedBullets = bullets.map((b) => b.trim()).filter(Boolean);
      const usedLabels = new Set(trimmedBullets.map(bulletLabel).filter((l): l is string => !!l));
      const prunedLinks = Object.fromEntries(
        Object.entries(companyLinks).filter(([label, url]) => usedLabels.has(label) && url.trim())
      );
      await onSave({
        name: name.trim(),
        intro: intro.trim(),
        linkedin: linkedin.trim() || null,
        github: github.trim() || null,
        website: website.trim() || null,
        experience_pool: trimmedBullets,
        company_links: prunedLinks,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <div style={fieldWrapStyle}>
        <label style={labelStyle}>Name</label>
        <input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} required />
      </div>

      <div style={fieldWrapStyle}>
        <label style={labelStyle}>Intro</label>
        <textarea
          style={{ ...inputStyle, minHeight: '80px', resize: 'vertical', fontFamily: 'inherit' }}
          value={intro}
          onChange={(e) => setIntro(e.target.value)}
          required
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1rem', marginBottom: '1.1rem' }}>
        <div>
          <label style={labelStyle}>LinkedIn</label>
          <input style={inputStyle} value={linkedin ?? ''} onChange={(e) => setLinkedin(e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>GitHub</label>
          <input style={inputStyle} value={github ?? ''} onChange={(e) => setGithub(e.target.value)} />
        </div>
        <div>
          <label style={labelStyle}>Website</label>
          <input style={inputStyle} value={website ?? ''} onChange={(e) => setWebsite(e.target.value)} />
        </div>
      </div>

      <div style={fieldWrapStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <label style={labelStyle}>Experience bullets</label>
          <button
            type="button"
            onClick={addBullet}
            style={{ ...iconButtonStyle, width: 'auto', padding: '0 10px', gap: '6px' }}
          >
            <Plus size={14} /> <span style={{ fontSize: '0.8rem' }}>Add</span>
          </button>
        </div>
        <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.6rem' }}>
          Format each bullet as <code>Company: achievement</code> — the part before
          the colon is bolded, and linked below if you give it a URL. Bullets without
          this exact <code>: </code> separator are rejected when saving.
        </p>
        {bullets.map((bullet, i) => {
          const isInvalid = invalidBullets.some((entry) => entry.i === i);
          return (
            <div key={i} style={{ display: 'flex', gap: '8px', marginBottom: '8px', alignItems: 'center' }}>
              <input
                style={{
                  ...inputStyle,
                  borderColor: isInvalid ? 'var(--danger)' : 'var(--border-color)',
                }}
                placeholder="Acme Inc: shipped the checkout redesign"
                value={bullet}
                onChange={(e) => updateBullet(i, e.target.value)}
              />
              <button type="button" onClick={() => moveBullet(i, -1)} disabled={i === 0} style={iconButtonStyle}>
                <ArrowUp size={14} />
              </button>
              <button
                type="button"
                onClick={() => moveBullet(i, 1)}
                disabled={i === bullets.length - 1}
                style={iconButtonStyle}
              >
                <ArrowDown size={14} />
              </button>
              <button
                type="button"
                onClick={() => removeBullet(i)}
                style={{ ...iconButtonStyle, color: 'var(--danger)' }}
              >
                <Trash2 size={14} />
              </button>
            </div>
          );
        })}
        {bullets.length === 0 && (
          <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>No bullets yet — add at least one.</p>
        )}
      </div>

      {linkableLabels.length > 0 && (
        <div style={fieldWrapStyle}>
          <label style={labelStyle}>Company links (optional)</label>
          <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.6rem' }}>
            Give any of these labels a URL to make the bolded name clickable in the email.
          </p>
          {linkableLabels.map((label) => (
            <div key={label} style={{ display: 'flex', gap: '8px', marginBottom: '8px', alignItems: 'center' }}>
              <span
                style={{
                  width: '160px',
                  flexShrink: 0,
                  fontSize: '0.8rem',
                  color: 'var(--text-secondary)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {label}
              </span>
              <input
                style={inputStyle}
                placeholder="https://..."
                value={companyLinks[label] ?? ''}
                onChange={(e) => setCompanyLinks((prev) => ({ ...prev, [label]: e.target.value }))}
              />
            </div>
          ))}
        </div>
      )}

      {error && <p style={{ color: 'var(--danger)', fontSize: '0.85rem', marginBottom: '1rem' }}>{error}</p>}

      <button
        type="submit"
        disabled={saving}
        style={{
          padding: '12px 24px',
          borderRadius: '10px',
          backgroundColor: 'var(--accent-primary)',
          color: '#fff',
          border: 'none',
          fontWeight: 700,
          fontSize: '0.9rem',
          cursor: saving ? 'not-allowed' : 'pointer',
        }}
      >
        {saving ? 'Saving…' : (saveLabel ?? 'Save profile')}
      </button>
    </form>
  );
}
