'use client';

import React, { useState } from 'react';
import {
  Send,
  CheckCircle,
  XCircle,
  RefreshCw,
  User,
  ExternalLink,
  Copy,
  Check,
} from 'lucide-react';
import type { LeadDraft, LeadItem } from '@/lib/api';

interface ReviewDeckProps {
  leads: LeadItem[];
  loading: boolean;
  /** id of the lead whose action is in flight, or null */
  actionLoading: string | null;
  onApprove: (lead: LeadItem) => void;
  /** Resolves true when the rejection succeeded, so the modal only closes then. */
  onReject: (leadId: string, notes: string) => Promise<boolean>;
  onRegenerate: (lead: LeadItem) => void;
  onTriggerDiscovery: () => void;
  /** Gates the empty-state Run Discovery button. Cosmetic only — see below. */
  isAdmin: boolean;
}

/** Presentational: the draft review queue and its reject modal. */
export default function ReviewDeck({
  leads,
  loading,
  actionLoading,
  onApprove,
  onReject,
  onRegenerate,
  onTriggerDiscovery,
  isAdmin,
}: ReviewDeckProps) {
  const [rejectingLeadId, setRejectingLeadId] = useState<string | null>(null);
  const [rejectNotes, setRejectNotes] = useState<string>('');
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const copyDraft = (draft: LeadDraft | null | undefined, id: string) => {
    if (!draft) return;
    const text = `Subject: ${draft.subject_line}\n\n${draft.body}`;
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleRejectConfirm = async () => {
    if (!rejectingLeadId) return;
    const succeeded = await onReject(rejectingLeadId, rejectNotes);
    if (succeeded) {
      setRejectingLeadId(null);
      setRejectNotes('');
    }
  };

  return (
    <div>
      {loading ? (
        <div style={{ textAlign: 'center', padding: '4rem', color: 'var(--text-muted)' }}>
          <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite' }} />
          <p style={{ marginTop: '1rem' }}>Loading review queue...</p>
        </div>
      ) : leads.length === 0 ? (
        <div
          style={{
            textAlign: 'center',
            padding: '5rem 2rem',
            backgroundColor: 'var(--bg-secondary)',
            borderRadius: '16px',
            border: '1px solid var(--border-color)',
          }}
        >
          <CheckCircle size={48} color="#10b981" style={{ margin: '0 auto 1rem' }} />
          <h3 style={{ fontSize: '1.25rem', fontWeight: 700 }}>Review Queue is Clear!</h3>
          <p style={{ color: 'var(--text-secondary)', maxWidth: '450px', margin: '0.5rem auto 1.5rem' }}>
            All generated drafts have been approved or dispatched. Run discovery or drafting sweep to populate new leads.
          </p>
          <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
            {/* Cosmetic only. `require_admin` on POST /api/pipeline/discovery is
                the real boundary — hiding a button is not authorization. Gated
                here so a non-admin isn't offered a control that only 403s. */}
            {isAdmin && (
              <button
                onClick={onTriggerDiscovery}
                style={{
                  padding: '10px 20px',
                  borderRadius: '10px',
                  backgroundColor: 'var(--accent-primary)',
                  color: '#fff',
                  border: 'none',
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Run Discovery
              </button>
            )}
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          {leads.map((lead) => (
            <div
              key={lead.id}
              style={{
                backgroundColor: 'var(--bg-card)',
                border: '1px solid var(--border-color)',
                borderRadius: '16px',
                padding: '1.5rem',
                boxShadow: 'var(--shadow-sm)',
                display: 'grid',
                gridTemplateColumns: 'minmax(280px, 1fr) minmax(340px, 1.4fr)',
                gap: '1.5rem',
              }}
            >
              {/* Lead Info & Research Column */}
              <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <h2 style={{ fontSize: '1.25rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {lead.company_name}
                      {lead.company_url && (
                        <a
                          href={lead.company_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: 'var(--text-muted)', display: 'inline-flex' }}
                        >
                          <ExternalLink size={14} />
                        </a>
                      )}
                    </h2>
                    <span
                      style={{
                        fontSize: '0.75rem',
                        padding: '3px 8px',
                        borderRadius: '6px',
                        backgroundColor: 'rgba(245, 158, 11, 0.15)',
                        color: '#fbbf24',
                        fontWeight: 600,
                      }}
                    >
                      Draft v{lead.draft?.version ?? 1}
                    </span>
                  </div>

                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '0.5rem', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    {lead.funding_stage && (
                      <span style={{ padding: '2px 8px', borderRadius: '4px', backgroundColor: 'var(--bg-secondary)' }}>
                        {lead.funding_stage}
                      </span>
                    )}
                    {lead.headcount && (
                      <span style={{ padding: '2px 8px', borderRadius: '4px', backgroundColor: 'var(--bg-secondary)' }}>
                        {lead.headcount} employees
                      </span>
                    )}
                  </div>

                  {/* Founder Info */}
                  <div
                    style={{
                      marginTop: '1.2rem',
                      padding: '12px',
                      borderRadius: '10px',
                      backgroundColor: 'var(--bg-secondary)',
                      border: '1px solid var(--border-color)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 600, fontSize: '0.9rem' }}>
                      <User size={14} color="#818cf8" />
                      {lead.founder_name || 'Founder'}
                    </div>
                    {lead.founder_email && (
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '4px' }}>
                        {lead.founder_email}
                      </div>
                    )}
                    {lead.linkedin_url && (
                      <a
                        href={lead.linkedin_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ fontSize: '0.75rem', color: '#818cf8', display: 'inline-flex', alignItems: 'center', gap: '4px', marginTop: '6px' }}
                      >
                        LinkedIn Profile <ExternalLink size={11} />
                      </a>
                    )}
                  </div>

                  {/* Research Hook */}
                  {lead.research?.hook && (
                    <div style={{ marginTop: '1rem' }}>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase' }}>
                        Synthesized Angle / Hook
                      </div>
                      <p style={{ fontSize: '0.85rem', color: '#cbd5e1', marginTop: '4px', lineHeight: '1.4' }}>
                        {lead.research.hook}
                      </p>
                    </div>
                  )}
                </div>

                {/* Action Buttons */}
                <div style={{ display: 'flex', gap: '8px', marginTop: '1.5rem' }}>
                  <button
                    onClick={() => onApprove(lead)}
                    disabled={actionLoading === lead.id}
                    style={{
                      flex: 1,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '6px',
                      padding: '10px',
                      borderRadius: '8px',
                      backgroundColor: '#10b981',
                      color: '#fff',
                      border: 'none',
                      fontWeight: 700,
                      fontSize: '0.85rem',
                      cursor: 'pointer',
                      transition: 'background 0.2s',
                    }}
                  >
                    <Send size={15} />
                    Approve & Send
                  </button>

                  <button
                    onClick={() => setRejectingLeadId(lead.id)}
                    disabled={actionLoading === lead.id}
                    style={{
                      padding: '10px 14px',
                      borderRadius: '8px',
                      backgroundColor: 'rgba(239, 68, 68, 0.15)',
                      color: '#f87171',
                      border: '1px solid rgba(239, 68, 68, 0.3)',
                      fontWeight: 600,
                      fontSize: '0.85rem',
                      cursor: 'pointer',
                    }}
                  >
                    <XCircle size={15} />
                  </button>

                  <button
                    onClick={() => onRegenerate(lead)}
                    disabled={actionLoading === lead.id}
                    title="Regenerate Draft"
                    style={{
                      padding: '10px 14px',
                      borderRadius: '8px',
                      backgroundColor: 'var(--bg-secondary)',
                      color: 'var(--text-secondary)',
                      border: '1px solid var(--border-color)',
                      fontWeight: 600,
                      fontSize: '0.85rem',
                      cursor: 'pointer',
                    }}
                  >
                    <RefreshCw size={15} />
                  </button>
                </div>
              </div>

              {/* Email Draft Preview Column */}
              <div
                style={{
                  backgroundColor: 'var(--bg-secondary)',
                  borderRadius: '12px',
                  padding: '1.25rem',
                  border: '1px solid var(--border-color)',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                }}
              >
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 700, textTransform: 'uppercase' }}>
                      Personalized Outreach
                    </span>
                    <button
                      onClick={() => copyDraft(lead.draft, lead.id)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                        background: 'none',
                        border: 'none',
                        color: copiedId === lead.id ? '#10b981' : 'var(--text-muted)',
                        fontSize: '0.75rem',
                        cursor: 'pointer',
                      }}
                    >
                      {copiedId === lead.id ? <Check size={13} /> : <Copy size={13} />}
                      {copiedId === lead.id ? 'Copied' : 'Copy'}
                    </button>
                  </div>

                  <div style={{ fontSize: '0.9rem', fontWeight: 700, color: '#f8fafc', marginBottom: '0.75rem' }}>
                    Subject: <span style={{ color: '#93c5fd', fontWeight: 600 }}>{lead.draft?.subject_line}</span>
                  </div>

                  <div
                    style={{
                      fontSize: '0.85rem',
                      color: '#cbd5e1',
                      whiteSpace: 'pre-wrap',
                      lineHeight: '1.5',
                      backgroundColor: 'rgba(0,0,0,0.2)',
                      padding: '1rem',
                      borderRadius: '8px',
                      border: '1px solid rgba(255,255,255,0.05)',
                    }}
                  >
                    {lead.draft?.body}
                  </div>
                </div>

                {lead.draft?.gmail_draft_id && (
                  <div style={{ fontSize: '0.75rem', color: '#a78bfa', marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <CheckCircle size={12} /> Stored in Gmail Drafts (ID: {lead.draft.gmail_draft_id.slice(0, 10)}...)
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Reject Modal */}
      {rejectingLeadId && (
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
              maxWidth: '440px',
            }}
          >
            <h3 style={{ fontSize: '1.15rem', fontWeight: 700 }}>Reject Lead Draft</h3>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '0.35rem' }}>
              Add an optional reason or feedback for rejection:
            </p>
            <textarea
              placeholder="E.g. Irrelevant product fit, wrong company size..."
              value={rejectNotes}
              onChange={(e) => setRejectNotes(e.target.value)}
              rows={3}
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
                resize: 'none',
              }}
            />
            <div style={{ display: 'flex', gap: '10px', marginTop: '1.25rem', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setRejectingLeadId(null)}
                style={{
                  padding: '8px 16px',
                  borderRadius: '8px',
                  backgroundColor: 'transparent',
                  color: 'var(--text-secondary)',
                  border: '1px solid var(--border-color)',
                  fontWeight: 600,
                  fontSize: '0.85rem',
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleRejectConfirm}
                style={{
                  padding: '8px 16px',
                  borderRadius: '8px',
                  backgroundColor: '#ef4444',
                  color: '#fff',
                  border: 'none',
                  fontWeight: 600,
                  fontSize: '0.85rem',
                  cursor: 'pointer',
                }}
              >
                Confirm Reject
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
