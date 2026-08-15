'use client';

import React from 'react';
import { CalendarClock, X } from 'lucide-react';
import type { ScheduledItem } from '@/lib/api';

interface ScheduledQueueProps {
  items: ScheduledItem[];
  loading: boolean;
  /** Display timezone (the caller's cadence zone, or the browser's if no
   * cadence is configured) -- shown alongside a UTC offset so "9am" always
   * means something concrete. */
  displayTimezone: string;
  actionLoading: string | null;
  onUnschedule: (outreachId: string) => void;
}

/** "Mon, Aug 17, 9:00 AM (UTC-4)" -- the zone name plus its current offset,
 * since a bare local time is ambiguous without one. */
function formatScheduled(iso: string, timeZone: string): string {
  const date = new Date(iso);
  const body = new Intl.DateTimeFormat('en-US', {
    timeZone,
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);

  const offsetPart = new Intl.DateTimeFormat('en-US', {
    timeZone,
    timeZoneName: 'shortOffset',
  })
    .formatToParts(date)
    .find((part) => part.type === 'timeZoneName');

  return offsetPart ? `${body} (${offsetPart.value})` : body;
}

/**
 * Presentational: the caller's upcoming approved-and-scheduled sends.
 *
 * The list itself and the unschedule mutation live in page.tsx; this
 * component only renders and asks for an unschedule via the callback.
 */
export default function ScheduledQueue({
  items,
  loading,
  displayTimezone,
  actionLoading,
  onUnschedule,
}: ScheduledQueueProps) {
  if (loading) {
    return <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Loading scheduled sends…</p>;
  }

  if (items.length === 0) {
    return (
      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Nothing scheduled right now.</p>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {items.map((item) => (
        <div
          key={item.outreach_id}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '10px 14px',
            borderRadius: '10px',
            backgroundColor: 'var(--bg-secondary)',
            border: '1px solid var(--border-color)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.85rem' }}>
            <CalendarClock size={15} color="#818cf8" />
            <span style={{ fontWeight: 600 }}>{item.company_name}</span>
            <span style={{ color: 'var(--text-secondary)' }}>
              {formatScheduled(item.scheduled_send_at, displayTimezone)}
            </span>
          </div>
          <button
            onClick={() => onUnschedule(item.outreach_id)}
            disabled={actionLoading === item.outreach_id}
            title="Unschedule"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              padding: '6px 10px',
              borderRadius: '8px',
              backgroundColor: 'rgba(239, 68, 68, 0.15)',
              color: '#f87171',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              fontWeight: 600,
              fontSize: '0.75rem',
              cursor: actionLoading === item.outreach_id ? 'not-allowed' : 'pointer',
            }}
          >
            <X size={12} />
            Unschedule
          </button>
        </div>
      ))}
    </div>
  );
}
