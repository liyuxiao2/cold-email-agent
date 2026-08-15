'use client';

import React from 'react';
import { ExternalLink, Search } from 'lucide-react';
import type { LeadItem } from '@/lib/api';

interface LeadExplorerProps {
  leads: LeadItem[];
  statusFilter: string;
  searchQuery: string;
  onStatusFilterChange: (status: string) => void;
  onSearchQueryChange: (search: string) => void;
}

const STATUSES = ['all', 'found', 'researched', 'drafted', 'approved', 'sent', 'rejected', 'failed'];

/**
 * Presentational: the all-leads table.
 *
 * The filters live in page.tsx rather than here on purpose: this component
 * unmounts on a tab switch, and local state would reset the filters every time
 * the user left and came back.
 */
export default function LeadExplorer({
  leads,
  statusFilter,
  searchQuery,
  onStatusFilterChange,
  onSearchQueryChange,
}: LeadExplorerProps) {
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

      {/* Leads Table */}
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
            {leads.map((lead) => (
              <tr key={lead.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                <td style={{ padding: '12px 16px', fontWeight: 600 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    {lead.company_name}
                    {lead.company_url && (
                      <a href={lead.company_url} target="_blank" rel="noreferrer" style={{ color: 'var(--text-muted)' }}>
                        <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                </td>
                <td style={{ padding: '12px 16px', color: 'var(--text-secondary)' }}>
                  <div>{lead.founder_name || '—'}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{lead.founder_email || ''}</div>
                </td>
                <td style={{ padding: '12px 16px', color: 'var(--text-secondary)' }}>
                  {lead.funding_stage || '—'}
                </td>
                <td style={{ padding: '12px 16px' }}>
                  <span
                    style={{
                      fontSize: '0.75rem',
                      padding: '3px 8px',
                      borderRadius: '6px',
                      textTransform: 'uppercase',
                      fontWeight: 700,
                      backgroundColor:
                        lead.status === 'approved' || lead.status === 'sent'
                          ? 'rgba(16, 185, 129, 0.15)'
                          : lead.status === 'drafted'
                          ? 'rgba(245, 158, 11, 0.15)'
                          : lead.status === 'rejected'
                          ? 'rgba(239, 68, 68, 0.15)'
                          : 'rgba(148, 163, 184, 0.15)',
                      color:
                        lead.status === 'approved' || lead.status === 'sent'
                          ? '#34d399'
                          : lead.status === 'drafted'
                          ? '#fbbf24'
                          : lead.status === 'rejected'
                          ? '#f87171'
                          : '#94a3b8',
                    }}
                  >
                    {lead.status}
                  </span>
                </td>
                <td style={{ padding: '12px 16px', color: '#cbd5e1', maxWidth: '300px' }}>
                  <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {lead.research?.hook || lead.error_msg || (lead.draft ? lead.draft.subject_line : '—')}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
