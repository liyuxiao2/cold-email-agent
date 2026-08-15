'use client';

import React, { useEffect, useState } from 'react';
import { Calendar, X } from 'lucide-react';

interface ScheduleDialogProps {
  open: boolean;
  companyName: string;
  submitting: boolean;
  onClose: () => void;
  /** `whenIso` is null for "send now" (overrides any cadence). */
  onConfirm: (whenIso: string | null) => void;
}

/**
 * Presentational: "Approve & schedule…" — a datetime-local input plus a
 * "Send now instead" escape hatch, for the one-off case where a user wants a
 * specific instant rather than the caller's default cadence slot.
 *
 * Owns only the input's local string state. The UTC conversion and the
 * approve call itself live in page.tsx, same as every other mutation.
 */
export default function ScheduleDialog({
  open,
  companyName,
  submitting,
  onClose,
  onConfirm,
}: ScheduleDialogProps) {
  const [localValue, setLocalValue] = useState<string>('');

  // `if (!open) return null` below only skips rendering -- it does not
  // unmount the component, so localValue would otherwise survive a close and
  // reappear pre-filled the next time this dialog opens for a DIFFERENT
  // company. Reset on every open so each company starts from a blank input.
  useEffect(() => {
    if (open) setLocalValue('');
  }, [open]);

  if (!open) return null;

  const handleConfirm = () => {
    if (!localValue) return;
    // datetime-local yields a naive local string (e.g. "2026-08-20T09:00")
    // with no timezone info; new Date(...) parses it in the BROWSER's local
    // zone, and toISOString() converts that instant to UTC, which is what
    // the API expects.
    const iso = new Date(localValue).toISOString();
    onConfirm(iso);
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.75)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        style={{
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border-color)',
          borderRadius: '16px',
          padding: '1.5rem',
          width: '90%',
          maxWidth: '420px',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Calendar size={18} color="#818cf8" />
            <h3 style={{ fontSize: '1.1rem', fontWeight: 700 }}>Schedule send</h3>
          </div>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}
          >
            <X size={18} />
          </button>
        </div>
        <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '0.35rem' }}>
          Pick a send time for {companyName || 'this company'}, or send it now instead.
        </p>

        <input
          type="datetime-local"
          value={localValue}
          onChange={(e) => setLocalValue(e.target.value)}
          style={{
            width: '100%',
            marginTop: '1rem',
            padding: '10px',
            borderRadius: '8px',
            backgroundColor: 'var(--bg-primary)',
            border: '1px solid var(--border-color)',
            color: 'var(--text-primary)',
            fontSize: '0.85rem',
            outline: 'none',
          }}
        />

        <div style={{ display: 'flex', gap: '10px', marginTop: '1.25rem', justifyContent: 'space-between' }}>
          <button
            onClick={() => onConfirm(null)}
            disabled={submitting}
            style={{
              padding: '8px 16px',
              borderRadius: '8px',
              backgroundColor: 'var(--bg-primary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-color)',
              fontWeight: 600,
              fontSize: '0.85rem',
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
          >
            Send now instead
          </button>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button
              onClick={onClose}
              disabled={submitting}
              style={{
                padding: '8px 16px',
                borderRadius: '8px',
                backgroundColor: 'transparent',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border-color)',
                fontWeight: 600,
                fontSize: '0.85rem',
                cursor: submitting ? 'not-allowed' : 'pointer',
              }}
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={submitting || !localValue}
              style={{
                padding: '8px 16px',
                borderRadius: '8px',
                backgroundColor: '#10b981',
                color: '#fff',
                border: 'none',
                fontWeight: 600,
                fontSize: '0.85rem',
                cursor: submitting || !localValue ? 'not-allowed' : 'pointer',
              }}
            >
              Schedule
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
