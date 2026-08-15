'use client';

import React from 'react';

interface QuotaBarProps {
  used: number;
  limit: number;
}

/**
 * Presentational: this month's draft-quota progress bar.
 *
 * Rendered by CompanyPool ABOVE the list, before any company is selected —
 * a user selecting 60 companies against a remaining quota of 12 should learn
 * that while choosing, not after submitting and getting a pile of
 * `quota_exceeded` skips back.
 */
export default function QuotaBar({ used, limit }: QuotaBarProps) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 100;
  const nearLimit = pct > 90;

  return (
    <div
      style={{
        marginBottom: '1.5rem',
        padding: '14px 16px',
        borderRadius: '12px',
        backgroundColor: 'var(--bg-card)',
        border: `1px solid ${nearLimit ? 'var(--warning)' : 'var(--border-color)'}`,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: '0.85rem',
          marginBottom: '8px',
        }}
      >
        <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>Drafts this month</span>
        <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>
          {used} / {limit}
        </span>
      </div>
      <div
        style={{
          height: '8px',
          width: '100%',
          borderRadius: '999px',
          backgroundColor: 'var(--bg-secondary)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${pct}%`,
            borderRadius: '999px',
            backgroundColor: nearLimit ? 'var(--danger)' : 'var(--accent-primary)',
            transition: 'width 0.2s ease',
          }}
        />
      </div>
    </div>
  );
}
