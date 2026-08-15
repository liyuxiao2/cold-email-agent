'use client';

import React from 'react';
import type { PipelineStats as PipelineStatsData } from '@/lib/api';

interface PipelineStatsProps {
  stats: PipelineStatsData | null;
  loading: boolean;
}

/**
 * Presentational: the pipeline counters bar. Owns no state.
 *
 * /api/pipeline/stats returns two dicts after the tenancy split: `companies`
 * (research_status, GLOBAL — the whole pool) and `outreach` (status,
 * PER-USER — only the caller's own rows). Both are shown, clearly labeled, so
 * a user does not mistake the global pool size for their own progress.
 */
export default function PipelineStats({ stats, loading }: PipelineStatsProps) {
  const companies = stats?.companies ?? {};
  const outreach = stats?.outreach ?? {};

  const tiles = [
    { label: 'Companies Found', count: companies.found ?? 0, color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.08)' },
    { label: 'Companies Researched', count: companies.researched ?? 0, color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.08)' },
    { label: 'My Queue', count: outreach.queued ?? 0, color: '#818cf8', bg: 'rgba(129, 140, 248, 0.08)' },
    { label: 'My Review Queue', count: outreach.drafted ?? 0, color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.12)', highlight: true },
    { label: 'My Approved', count: outreach.approved ?? 0, color: '#34d399', bg: 'rgba(52, 211, 153, 0.08)' },
    { label: 'My Sent', count: outreach.sent ?? 0, color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.08)' },
    { label: 'My Rejected', count: outreach.rejected ?? 0, color: '#f87171', bg: 'rgba(248, 113, 113, 0.08)' },
  ];

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
        gap: '12px',
        marginBottom: '2rem',
      }}
    >
      {tiles.map((item, idx) => (
        <div
          key={idx}
          style={{
            padding: '14px 16px',
            borderRadius: '12px',
            backgroundColor: item.bg,
            border: item.highlight ? '1px solid rgba(245, 158, 11, 0.4)' : '1px solid var(--border-color)',
            boxShadow: item.highlight ? '0 0 15px rgba(245, 158, 11, 0.15)' : 'none',
          }}
        >
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {item.label}
          </div>
          <div style={{ fontSize: '1.6rem', fontWeight: 800, color: item.color, marginTop: '4px' }}>
            {loading ? '—' : item.count}
          </div>
        </div>
      ))}
    </div>
  );
}
