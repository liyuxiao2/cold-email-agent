'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Clock, Save, Trash2 } from 'lucide-react';
import type { Cadence } from '@/lib/api';

interface CadenceSettingsProps {
  cadence: Cadence | null;
  saving: boolean;
  onSave: (cadence: Cadence) => void;
  onClear: () => void;
}

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const DEFAULT_CADENCE: Cadence = {
  max_per_day: 10,
  days: [0, 1, 2, 3, 4],
  window_start: '09:00',
  window_end: '17:00',
  timezone: typeof Intl !== 'undefined' ? Intl.DateTimeFormat().resolvedOptions().timeZone : 'UTC',
};

/** Renders one UTC instant as calendar-date parts (y, m, d, hh, mm) in `timeZone`. */
function localDateParts(date: Date, timeZone: string) {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
  const parts: Record<string, string> = {};
  for (const part of fmt.formatToParts(date)) parts[part.type] = part.value;
  return {
    y: Number(parts.year),
    m: Number(parts.month),
    d: Number(parts.day),
    hh: Number(parts.hour),
    mm: Number(parts.minute),
  };
}

/** The wall-clock (y, m, d, hh, mm) in `timeZone`, converted to its real UTC
 * instant. Self-contained (no date library): guesses the instant by treating
 * the wall time as UTC, measures how far that guess renders from the target
 * wall time in `timeZone`, and corrects by the difference. This is a preview
 * only -- the server (cold_email/cadence.py) is the source of truth for what
 * actually gets scheduled. */
function zonedTimeToUtc(y: number, m: number, d: number, hh: number, mm: number, timeZone: string): Date {
  const naiveUtc = Date.UTC(y, m - 1, d, hh, mm, 0);
  const rendered = localDateParts(new Date(naiveUtc), timeZone);
  const renderedAsUtc = Date.UTC(rendered.y, rendered.m - 1, rendered.d, rendered.hh, rendered.mm, 0);
  return new Date(naiveUtc - (renderedAsUtc - naiveUtc));
}

/** 0=Monday..6=Sunday, matching cadence.py's `days` convention (Python's
 * date.weekday()) -- NOT JS's native 0=Sunday. */
function pyWeekday(y: number, m: number, d: number): number {
  const jsDay = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return (jsDay + 6) % 7;
}

function addCalendarDays(y: number, m: number, d: number, days: number): [number, number, number] {
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return [dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate()];
}

/** Evenly-spaced offsets (in minutes from window_start) for `count` slots --
 * mirrors cadence.py's _slot_offsets, so the preview matches server
 * behaviour: N emails at the same instant is the burst cadence exists to
 * prevent. */
function slotOffsetMinutes(windowStart: string, windowEnd: string, count: number): number[] {
  const [sh, sm] = windowStart.split(':').map(Number);
  const [eh, em] = windowEnd.split(':').map(Number);
  const spanMin = eh * 60 + em - (sh * 60 + sm);
  if (count <= 0 || spanMin <= 0) return [];
  const step = spanMin / count;
  return Array.from({ length: count }, (_, i) => Math.round(step * i));
}

/** Computes the next `count` slots this cadence would produce, ignoring any
 * already-booked sends (a fresh preview of the cadence's shape, not a
 * booking simulation). */
function previewNextSlots(cadence: Cadence, count: number): Date[] {
  const now = new Date();
  const today = localDateParts(now, cadence.timezone);
  const [sh, sm] = cadence.window_start.split(':').map(Number);
  const offsets = slotOffsetMinutes(cadence.window_start, cadence.window_end, cadence.max_per_day);
  const results: Date[] = [];

  for (let dayIndex = 0; dayIndex < 90 && results.length < count; dayIndex++) {
    const [y, m, d] = addCalendarDays(today.y, today.m, today.d, dayIndex);
    if (!cadence.days.includes(pyWeekday(y, m, d))) continue;

    for (const offsetMin of offsets) {
      const totalMin = sh * 60 + sm + offsetMin;
      const hh = Math.floor(totalMin / 60);
      const mm = totalMin % 60;
      const candidate = zonedTimeToUtc(y, m, d, hh, mm, cadence.timezone);
      if (candidate < now) continue;
      results.push(candidate);
      if (results.length >= count) break;
    }
  }
  return results;
}

/** "Mon, 9:00 AM (UTC-4)" -- the zone's own offset alongside the time, same
 * as ScheduledQueue.tsx's formatScheduled: a bare local time is ambiguous
 * without one, and this preview's whole point is showing the user exactly
 * when a slot lands. */
function formatSlot(date: Date, timeZone: string): string {
  const body = new Intl.DateTimeFormat('en-US', {
    timeZone,
    weekday: 'short',
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

/** Preview of the next few sends a draft cadence would produce -- a
 * cadence's real behaviour is not obvious from four fields, and a
 * misconfigured window silently delays a user's whole queue. */
function PreviewSlots({ cadence, count }: { cadence: Cadence; count: number }) {
  const slots = useMemo(() => {
    try {
      return previewNextSlots(cadence, count);
    } catch {
      return [];
    }
  }, [cadence, count]);

  if (slots.length === 0) {
    return (
      <p style={{ fontSize: '0.8rem', color: 'var(--warning)' }}>
        No slot found in this window with the current settings.
      </p>
    );
  }

  return (
    <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
      Next {slots.length} sends: {slots.map((s) => formatSlot(s, cadence.timezone)).join(', ')}
    </p>
  );
}

const COMMON_TIMEZONES = [
  Intl.DateTimeFormat().resolvedOptions().timeZone,
  'America/Toronto',
  'America/New_York',
  'America/Los_Angeles',
  'America/Chicago',
  'Europe/London',
  'UTC',
].filter((tz, index, all) => all.indexOf(tz) === index);

/**
 * Presentational: the per-user daily send-cadence form.
 *
 * Owns only the in-progress draft (uncommitted edits); `cadence` (the saved
 * value) and the save/clear mutations live in page.tsx.
 */
export default function CadenceSettings({ cadence, saving, onSave, onClear }: CadenceSettingsProps) {
  const [draft, setDraft] = useState<Cadence>(cadence ?? DEFAULT_CADENCE);

  // The initial GET /api/cadence resolves after this component's first
  // render (cadence starts null while page.tsx is still fetching), so the
  // useState initializer above only ever sees the fallback. Sync once the
  // real value arrives.
  useEffect(() => {
    if (cadence) setDraft(cadence);
  }, [cadence]);

  const toggleDay = (day: number) => {
    setDraft((prev) => ({
      ...prev,
      days: prev.days.includes(day) ? prev.days.filter((d) => d !== day) : [...prev.days, day].sort(),
    }));
  };

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        borderRadius: '16px',
        padding: '1.5rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 700, marginBottom: '0.25rem' }}>
        <Clock size={16} color="#818cf8" />
        Daily send cadence
      </div>
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.8rem', marginBottom: '1rem' }}>
        Spreads approved sends across the day instead of leaving one Gmail account all at
        once — the clearest spam signal a new sender can produce.
        {cadence ? '' : ' Not configured: approvals without an explicit time send on the next scan.'}
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
        <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          Max per day (1-50)
          <input
            type="number"
            min={1}
            max={50}
            value={draft.max_per_day}
            onChange={(e) => setDraft((prev) => ({ ...prev, max_per_day: Number(e.target.value) }))}
            style={inputStyle}
          />
        </label>
        <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          Timezone
          <select
            value={draft.timezone}
            onChange={(e) => setDraft((prev) => ({ ...prev, timezone: e.target.value }))}
            style={inputStyle}
          >
            {COMMON_TIMEZONES.map((tz) => (
              <option key={tz} value={tz}>
                {tz}
              </option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          Window start
          <input
            type="time"
            value={draft.window_start}
            onChange={(e) => setDraft((prev) => ({ ...prev, window_start: e.target.value }))}
            style={inputStyle}
          />
        </label>
        <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          Window end
          <input
            type="time"
            value={draft.window_end}
            onChange={(e) => setDraft((prev) => ({ ...prev, window_end: e.target.value }))}
            style={inputStyle}
          />
        </label>
      </div>

      <div style={{ display: 'flex', gap: '6px', marginBottom: '1rem' }}>
        {DAY_LABELS.map((label, day) => {
          const active = draft.days.includes(day);
          return (
            <button
              key={day}
              type="button"
              onClick={() => toggleDay(day)}
              style={{
                flex: 1,
                padding: '8px 0',
                borderRadius: '8px',
                fontSize: '0.75rem',
                fontWeight: 700,
                border: `1px solid ${active ? 'var(--accent-primary)' : 'var(--border-color)'}`,
                backgroundColor: active ? 'var(--accent-primary)' : 'var(--bg-secondary)',
                color: active ? '#fff' : 'var(--text-secondary)',
                cursor: 'pointer',
              }}
            >
              {label}
            </button>
          );
        })}
      </div>

      <div style={{ marginBottom: '1.25rem' }}>
        <PreviewSlots cadence={draft} count={5} />
      </div>

      <div style={{ display: 'flex', gap: '10px' }}>
        <button
          onClick={() => onSave(draft)}
          disabled={saving || draft.days.length === 0}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '10px 16px',
            borderRadius: '8px',
            backgroundColor: '#10b981',
            color: '#fff',
            border: 'none',
            fontWeight: 700,
            fontSize: '0.85rem',
            cursor: saving ? 'not-allowed' : 'pointer',
          }}
        >
          <Save size={14} />
          Save cadence
        </button>
        {cadence && (
          <button
            onClick={onClear}
            disabled={saving}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '10px 16px',
              borderRadius: '8px',
              backgroundColor: 'rgba(239, 68, 68, 0.15)',
              color: '#f87171',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              fontWeight: 600,
              fontSize: '0.85rem',
              cursor: saving ? 'not-allowed' : 'pointer',
            }}
          >
            <Trash2 size={14} />
            Send immediately instead
          </button>
        )}
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  marginTop: '4px',
  padding: '8px 10px',
  borderRadius: '8px',
  backgroundColor: 'var(--bg-primary)',
  border: '1px solid var(--border-color)',
  color: 'var(--text-primary)',
  fontSize: '0.85rem',
  outline: 'none',
};
