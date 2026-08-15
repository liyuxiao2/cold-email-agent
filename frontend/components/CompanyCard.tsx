'use client';

import React from 'react';
import { CheckCircle2, Circle, ExternalLink } from 'lucide-react';
import type { PoolCompany } from '@/lib/api';

interface CompanyCardProps {
  company: PoolCompany;
  selected: boolean;
  onToggle: (id: string) => void;
}

/**
 * Presentational: one selectable company card in the pool browser.
 *
 * Shows `contact_count` ("N contacts available") and `has_founder_contact`
 * ("founder reachable") ONLY — never an email address. GET /api/companies
 * deliberately omits addresses entirely (see the module docstring on
 * cold_email/api/routes/companies.py and the PoolCompany comment in
 * lib/api.ts); nothing rendered here should imply one is visible before a
 * contact is assigned via POST /api/outreach.
 */
export default function CompanyCard({ company, selected, onToggle }: CompanyCardProps) {
  const details = [
    company.industry,
    company.funding_stage,
    company.headcount ? `${company.headcount} people` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div
      onClick={() => onToggle(company.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onToggle(company.id);
      }}
      style={{
        padding: '16px',
        borderRadius: '12px',
        backgroundColor: 'var(--bg-card)',
        border: selected ? '1px solid var(--accent-primary)' : '1px solid var(--border-color)',
        boxShadow: selected ? '0 0 0 1px var(--accent-primary)' : 'none',
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 700 }}>
            {company.company_name}
            {company.company_url && (
              <a
                href={company.company_url}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
                style={{ color: 'var(--text-muted)' }}
              >
                <ExternalLink size={12} />
              </a>
            )}
          </div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '2px' }}>
            {details || '—'}
          </div>
        </div>
        {selected ? (
          <CheckCircle2 size={20} color="var(--accent-primary)" />
        ) : (
          <Circle size={20} color="var(--text-muted)" />
        )}
      </div>

      {company.research.hook && (
        <p style={{ fontSize: '0.85rem', color: '#cbd5e1', margin: 0 }}>{company.research.hook}</p>
      )}

      {company.research.tech_stack && company.research.tech_stack.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
          {company.research.tech_stack.slice(0, 6).map((tech) => (
            <span
              key={tech}
              style={{
                fontSize: '0.7rem',
                padding: '2px 8px',
                borderRadius: '999px',
                backgroundColor: 'var(--bg-secondary)',
                color: 'var(--text-secondary)',
              }}
            >
              {tech}
            </span>
          ))}
        </div>
      )}

      {/* contact_count / has_founder_contact only — never an email address.
          The API omits addresses from this response entirely; do not extend
          this line to imply one is visible here. */}
      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
        {company.contact_count} contact{company.contact_count === 1 ? '' : 's'} available
        {company.has_founder_contact && ' · founder reachable'}
      </span>
    </div>
  );
}
