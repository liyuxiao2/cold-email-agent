'use client';

import React from 'react';
import { RefreshCw, Sparkles } from 'lucide-react';

interface AdminPanelProps {
  triggeringDiscovery: boolean;
  triggeringDrafting: boolean;
  onTriggerDiscovery: () => void;
  onTriggerDrafting: () => void;
}

/** Presentational: the pipeline trigger buttons, rendered only for admins. */
export default function AdminPanel({
  triggeringDiscovery,
  triggeringDrafting,
  onTriggerDiscovery,
  onTriggerDrafting,
}: AdminPanelProps) {
  return (
    <>
      <button
        onClick={onTriggerDiscovery}
        disabled={triggeringDiscovery}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '10px 16px',
          borderRadius: '10px',
          backgroundColor: 'var(--bg-secondary)',
          color: 'var(--text-primary)',
          border: '1px solid var(--border-color)',
          fontWeight: 600,
          fontSize: '0.875rem',
          cursor: triggeringDiscovery ? 'not-allowed' : 'pointer',
          transition: 'all 0.2s',
        }}
      >
        <Sparkles size={16} color="#818cf8" />
        {triggeringDiscovery ? 'Running Discovery...' : 'Trigger Discovery'}
      </button>

      <button
        onClick={onTriggerDrafting}
        disabled={triggeringDrafting}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '10px 16px',
          borderRadius: '10px',
          backgroundColor: 'var(--bg-secondary)',
          color: 'var(--text-primary)',
          border: '1px solid var(--border-color)',
          fontWeight: 600,
          fontSize: '0.875rem',
          cursor: triggeringDrafting ? 'not-allowed' : 'pointer',
          transition: 'all 0.2s',
        }}
      >
        <RefreshCw size={16} color="#34d399" />
        {triggeringDrafting ? 'Drafting...' : 'Sweep Drafts'}
      </button>
    </>
  );
}
