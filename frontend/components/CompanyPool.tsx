'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  createOutreach,
  getPool,
  getQuota,
  type CreateOutreachResult,
  type PoolCompany,
  type QuotaStatus,
  type SkippedOutreach,
} from '@/lib/api';
import CompanyCard from '@/components/CompanyCard';
import QuotaBar from '@/components/QuotaBar';

const PAGE_SIZE = 20;

// Maps POST /api/outreach's `skipped[].reason` codes to plain English. Keep
// in sync with the reasons create_outreach() in outreach.py can emit.
const SKIP_REASON_LABELS: Record<string, string> = {
  no_available_contact: 'all contacts already reached',
  already_targeted: 'already in your list',
  quota_exceeded: 'over your monthly limit',
  not_researched: 'not researched yet',
};

function summarizeSkipped(skipped: SkippedOutreach[]): string {
  const counts = new Map<string, number>();
  for (const item of skipped) {
    const label = SKIP_REASON_LABELS[item.reason] ?? item.reason;
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([label, count]) => `${count} ${label}`)
    .join(', ');
}

const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));

const filterInputStyle: React.CSSProperties = {
  padding: '8px 12px',
  borderRadius: '8px',
  backgroundColor: 'var(--bg-secondary)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-primary)',
  fontSize: '0.85rem',
  outline: 'none',
};

/**
 * The company pool browser: filters, pagination, selection, and submission.
 *
 * Fetches its own data (pool page + quota) rather than receiving it as props
 * from app/pool/page.tsx. Unlike the main dashboard (app/page.tsx), this
 * route has exactly one consumer of this state, so there is nothing to lift.
 */
export default function CompanyPool() {
  const [companies, setCompanies] = useState<PoolCompany[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [industry, setIndustry] = useState('');
  const [fundingStage, setFundingStage] = useState('');
  const [headcountMin, setHeadcountMin] = useState('');
  const [headcountMax, setHeadcountMax] = useState('');
  const [search, setSearch] = useState('');
  const [founderOnly, setFounderOnly] = useState(false);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [quota, setQuota] = useState<QuotaStatus | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<CreateOutreachResult | null>(null);

  const loadQuota = useCallback(async () => {
    try {
      setQuota(await getQuota());
    } catch (err) {
      console.error(err);
    }
  }, []);

  const loadPool = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getPool({
        industry: industry || undefined,
        fundingStage: fundingStage || undefined,
        headcountMin: headcountMin ? Number(headcountMin) : undefined,
        headcountMax: headcountMax ? Number(headcountMax) : undefined,
        search: search || undefined,
        hasFounderContact: founderOnly || undefined,
        limit: PAGE_SIZE,
        offset,
      });
      setCompanies(page.items);
      setTotal(page.total);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [industry, fundingStage, headcountMin, headcountMax, search, founderOnly, offset]);

  useEffect(() => {
    loadQuota();
  }, [loadQuota]);

  // One debounce covers every filter, including free-text search, instead of
  // a separate code path for typing vs. the other controls.
  useEffect(() => {
    const timer = setTimeout(loadPool, 300);
    return () => clearTimeout(timer);
  }, [loadPool]);

  const resetToFirstPage = () => setOffset(0);

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const remaining = quota ? quota.limit - quota.used : null;
  const overQuota = remaining !== null && selected.size > remaining;

  const handleSubmit = async () => {
    if (selected.size === 0) return;
    setSubmitting(true);
    setResult(null);
    setError(null);
    try {
      const res = await createOutreach(Array.from(selected));
      setResult(res);
      setSelected(new Set());
      setQuota((prev) => (prev ? { ...prev, used: res.quota.used, limit: res.quota.limit } : prev));
      loadPool(); // selected companies may now have dropped out of the pool
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div>
      {/* Above the list, before selection: a user picking 60 against a
          remaining quota of 12 should learn that while choosing. */}
      {quota && <QuotaBar used={quota.used} limit={quota.limit} />}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginBottom: '1.5rem', alignItems: 'center' }}>
        <input
          style={{ ...filterInputStyle, width: '200px' }}
          placeholder="Search company..."
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            resetToFirstPage();
          }}
        />
        <input
          style={{ ...filterInputStyle, width: '140px' }}
          placeholder="Industry"
          value={industry}
          onChange={(e) => {
            setIndustry(e.target.value);
            resetToFirstPage();
          }}
        />
        <input
          style={{ ...filterInputStyle, width: '140px' }}
          placeholder="Funding stage"
          value={fundingStage}
          onChange={(e) => {
            setFundingStage(e.target.value);
            resetToFirstPage();
          }}
        />
        <input
          style={{ ...filterInputStyle, width: '100px' }}
          placeholder="Min size"
          type="number"
          min={0}
          value={headcountMin}
          onChange={(e) => {
            setHeadcountMin(e.target.value);
            resetToFirstPage();
          }}
        />
        <input
          style={{ ...filterInputStyle, width: '100px' }}
          placeholder="Max size"
          type="number"
          min={0}
          value={headcountMax}
          onChange={(e) => {
            setHeadcountMax(e.target.value);
            resetToFirstPage();
          }}
        />
        <label
          style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.85rem', color: 'var(--text-secondary)' }}
        >
          <input
            type="checkbox"
            checked={founderOnly}
            onChange={(e) => {
              setFounderOnly(e.target.checked);
              resetToFirstPage();
            }}
          />
          Founder reachable only
        </label>
      </div>

      {error && <p style={{ color: 'var(--danger)', fontSize: '0.85rem', marginBottom: '1rem' }}>{error}</p>}

      {loading ? (
        <p style={{ color: 'var(--text-muted)', padding: '2rem 0' }}>Loading companies…</p>
      ) : companies.length === 0 ? (
        <p style={{ color: 'var(--text-muted)', padding: '2rem 0' }}>No companies match these filters.</p>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: '12px',
            marginBottom: '1.5rem',
          }}
        >
          {companies.map((company) => (
            <CompanyCard
              key={company.id}
              company={company}
              selected={selected.has(company.id)}
              onToggle={toggleSelected}
            />
          ))}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={offset === 0}
            style={{ ...filterInputStyle, cursor: offset === 0 ? 'not-allowed' : 'pointer' }}
          >
            Previous
          </button>
          <button
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total}
            style={{ ...filterInputStyle, cursor: offset + PAGE_SIZE >= total ? 'not-allowed' : 'pointer' }}
          >
            Next
          </button>
        </div>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          Page {currentPage} of {totalPages} ({total} companies)
        </span>
      </div>

      {overQuota && (
        <p style={{ color: 'var(--warning)', fontSize: '0.85rem', marginBottom: '0.75rem' }}>
          You&apos;ve selected {selected.size} companies but only have {Math.max(0, remaining ?? 0)} draft
          {remaining === 1 ? '' : 's'} left this month — the rest will come back skipped as over your monthly
          limit.
        </p>
      )}

      <button
        onClick={handleSubmit}
        disabled={selected.size === 0 || submitting}
        style={{
          padding: '12px 24px',
          borderRadius: '10px',
          backgroundColor: 'var(--accent-primary)',
          color: '#fff',
          border: 'none',
          fontWeight: 700,
          fontSize: '0.9rem',
          cursor: selected.size === 0 || submitting ? 'not-allowed' : 'pointer',
          opacity: selected.size === 0 ? 0.6 : 1,
        }}
      >
        {submitting ? 'Queuing…' : `Draft these (${selected.size})`}
      </button>

      {result && (
        <div
          style={{
            marginTop: '1rem',
            padding: '12px 16px',
            borderRadius: '10px',
            backgroundColor: 'var(--success-bg)',
            border: '1px solid var(--success)',
            fontSize: '0.85rem',
          }}
        >
          <p style={{ margin: 0, color: 'var(--text-primary)' }}>
            {result.created.length} queued for drafting.
            {result.skipped.length > 0 && ` ${result.skipped.length} skipped: ${summarizeSkipped(result.skipped)}.`}
          </p>
        </div>
      )}
    </div>
  );
}
