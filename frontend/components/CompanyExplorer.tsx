'use client';

import React from 'react';
import { ExternalLink, Search } from 'lucide-react';
import type { CompanyItem } from '@/lib/api';

interface CompanyExplorerProps {
  companies: CompanyItem[];
  statusFilter: string;
  searchQuery: string;
  onStatusFilterChange: (status: string) => void;
  onSearchQueryChange: (search: string) => void;
}

// research_status is GLOBAL (found/researched/failed); 'queued' is included
// here too so the same badge palette also covers per-user outreach status
// values if this table is ever asked to render one.
const STATUSES = ['all', 'found', 'researched', 'queued', 'failed'];

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  approved: { bg: 'rgba(16, 185, 129, 0.15)', color: '#34d399' },
  sent: { bg: 'rgba(16, 185, 129, 0.15)', color: '#34d399' },
  researched: { bg: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8' },
  drafted: { bg: 'rgba(245, 158, 11, 0.15)', color: '#fbbf24' },
  queued: { bg: 'rgba(129, 140, 248, 0.15)', color: '#818cf8' },
  rejected: { bg: 'rgba(239, 68, 68, 0.15)', color: '#f87171' },
  failed: { bg: 'rgba(239, 68, 68, 0.15)', color: '#f87171' },
};
const DEFAULT_STATUS_COLOR = { bg: 'rgba(148, 163, 184, 0.15)', color: '#94a3b8' };

/**
 * Presentational: the global company-pool table.
 *
 * Consumes the GLOBAL /api/companies pool — every authenticated user sees the
 * same rows, unlike the per-user outreach queue in ReviewDeck. The filters
 * live in page.tsx rather than here on purpose: this component unmounts on a
 * tab switch, and local state would reset the filters every time the user
 * left and came back.
 */
export default function CompanyExplorer({
  companies,
  statusFilter,
  searchQuery,
  onStatusFilterChange,
  onSearchQueryChange,
}: CompanyExplorerProps) {
  return (
    <div>
      {/* Filters & Search */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '12px',
          justifyContent: 'space-between',
          marginBottom: '1.5rem',
        }}
      >
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {STATUSES.map((st) => (
            <button
              key={st}
              onClick={() => onStatusFilterChange(st)}
              style={{
                padding: '6px 14px',
                borderRadius: '8px',
                fontSize: '0.8rem',
                fontWeight: 600,
                border: statusFilter === st ? '1px solid var(--accent-primary)' : '1px solid var(--border-color)',
                backgroundColor: statusFilter === st ? 'rgba(99, 102, 241, 0.2)' : 'var(--bg-secondary)',
                color: statusFilter === st ? '#818cf8' : 'var(--text-secondary)',
                cursor: 'pointer',
                textTransform: 'capitalize',
              }}
            >
              {st}
            </button>
          ))}
        </div>

        <div style={{ position: 'relative', width: '260px' }}>
          <Search
            size={16}
            color="var(--text-muted)"
            style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)' }}
          />
          <input
            type="text"
            placeholder="Search company or founder..."
            value={searchQuery}
            onChange={(e) => onSearchQueryChange(e.target.value)}
            style={{
              width: '100%',
              padding: '8px 12px 8px 36px',
              borderRadius: '8px',
              backgroundColor: 'var(--bg-secondary)',
              border: '1px solid var(--border-color)',
              color: 'var(--text-primary)',
              fontSize: '0.85rem',
              outline: 'none',
            }}
          />
        </div>
      </div>

      {/* Companies Table */}
      <div
        style={{
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          borderRadius: '12px',
          overflow: 'hidden',
        }}
      >
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
          <thead>
            <tr style={{ backgroundColor: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-color)' }}>
              <th style={{ padding: '12px 16px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 600 }}>Company</th>
              <th style={{ padding: '12px 16px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 600 }}>Founder</th>
              <th style={{ padding: '12px 16px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 600 }}>Stage</th>
              <th style={{ padding: '12px 16px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 600 }}>Status</th>
              <th style={{ padding: '12px 16px', textAlign: 'left', color: 'var(--text-muted)', fontWeight: 600 }}>Hook / Details</th>
            </tr>
          </thead>
          <tbody>
            {companies.map((company) => {
              const statusStyle = STATUS_COLORS[company.research_status] ?? DEFAULT_STATUS_COLOR;
              return (
                <tr key={company.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                  <td style={{ padding: '12px 16px', fontWeight: 600 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      {company.company_name}
                      {company.company_url && (
                        <a href={company.company_url} target="_blank" rel="noreferrer" style={{ color: 'var(--text-muted)' }}>
                          <ExternalLink size={12} />
                        </a>
                      )}
                    </div>
                  </td>
                  <td style={{ padding: '12px 16px', color: 'var(--text-secondary)' }}>
                    <div>{company.founder_name || '—'}</div>
                  </td>
                  <td style={{ padding: '12px 16px', color: 'var(--text-secondary)' }}>
                    {company.funding_stage || '—'}
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    <span
                      style={{
                        fontSize: '0.75rem',
                        padding: '3px 8px',
                        borderRadius: '6px',
                        textTransform: 'uppercase',
                        fontWeight: 700,
                        backgroundColor: statusStyle.bg,
                        color: statusStyle.color,
                      }}
                    >
                      {company.research_status}
                    </span>
                  </td>
                  <td style={{ padding: '12px 16px', color: '#cbd5e1', maxWidth: '300px' }}>
                    <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {company.research?.hook || company.error_msg || '—'}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
