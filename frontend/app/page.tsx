'use client';

import React, { useEffect, useState } from 'react';
import {
  Send,
  CheckCircle,
  XCircle,
  RefreshCw,
  Search,
  Sparkles,
  Zap,
  Building,
  User,
  Mail,
  ExternalLink,
  ChevronRight,
  Filter,
  Copy,
  Check,
  AlertCircle
} from 'lucide-react';
import {
  LeadItem,
  PipelineStats,
  approveLead,
  fetchDraftQueue,
  fetchLeads,
  fetchPipelineStats,
  regenerateDraft,
  rejectLead,
  triggerDiscovery,
  triggerDrafting
} from '../lib/api';

export default function DashboardPage() {
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [draftQueue, setDraftQueue] = useState<LeadItem[]>([]);
  const [allLeads, setAllLeads] = useState<LeadItem[]>([]);
  const [activeTab, setActiveTab] = useState<'review' | 'explorer'>('review');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [triggeringDiscovery, setTriggeringDiscovery] = useState<boolean>(false);
  const [triggeringDrafting, setTriggeringDrafting] = useState<boolean>(false);
  const [notification, setNotification] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
  const [rejectingLeadId, setRejectingLeadId] = useState<string | null>(null);
  const [rejectNotes, setRejectNotes] = useState<string>('');
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const showNotification = (message: string, type: 'success' | 'error' = 'success') => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 4000);
  };

  const loadData = async () => {
    try {
      setLoading(true);
      const [statsData, queueData] = await Promise.all([
        fetchPipelineStats(),
        fetchDraftQueue(),
      ]);
      setStats(statsData);
      setDraftQueue(queueData);
    } catch (err: any) {
      console.error(err);
      showNotification(`Connection error: ${err.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const loadExplorerLeads = async () => {
    try {
      const filter = statusFilter === 'all' ? undefined : statusFilter;
      const data = await fetchLeads({ status: filter, search: searchQuery || undefined, limit: 100 });
      setAllLeads(data.items);
    } catch (err: any) {
      console.error(err);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    if (activeTab === 'explorer') {
      loadExplorerLeads();
    }
  }, [activeTab, statusFilter, searchQuery]);

  const handleApprove = async (lead: LeadItem) => {
    try {
      setActionLoading(lead.id);
      await approveLead(lead.id);
      setDraftQueue((prev) => prev.filter((item) => item.id !== lead.id));
      if (stats) setStats({ ...stats, drafted: Math.max(0, stats.drafted - 1), approved: stats.approved + 1 });
      showNotification(`Approved outreach for ${lead.company_name}! Dispatched logistics task.`);
    } catch (err: any) {
      showNotification(`Failed to approve: ${err.message}`, 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const handleRejectConfirm = async () => {
    if (!rejectingLeadId) return;
    try {
      setActionLoading(rejectingLeadId);
      await rejectLead(rejectingLeadId, rejectNotes);
      setDraftQueue((prev) => prev.filter((item) => item.id !== rejectingLeadId));
      if (stats) setStats({ ...stats, drafted: Math.max(0, stats.drafted - 1), rejected: stats.rejected + 1 });
      showNotification(`Rejected lead.`);
      setRejectingLeadId(null);
      setRejectNotes('');
    } catch (err: any) {
      showNotification(`Failed to reject: ${err.message}`, 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const handleRegenerate = async (lead: LeadItem) => {
    try {
      setActionLoading(lead.id);
      await regenerateDraft(lead.id);
      setDraftQueue((prev) => prev.filter((item) => item.id !== lead.id));
      if (stats) setStats({ ...stats, drafted: Math.max(0, stats.drafted - 1), researched: stats.researched + 1 });
      showNotification(`Draft queued for re-generation (${lead.company_name})`);
    } catch (err: any) {
      showNotification(`Failed to regenerate: ${err.message}`, 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const handleTriggerDiscovery = async () => {
    try {
      setTriggeringDiscovery(true);
      const res = await triggerDiscovery();
      showNotification(res.message || 'Discovery task queued on Celery!');
      setTimeout(loadData, 2000);
    } catch (err: any) {
      showNotification(`Discovery trigger error: ${err.message}`, 'error');
    } finally {
      setTriggeringDiscovery(false);
    }
  };

  const handleTriggerDrafting = async () => {
    try {
      setTriggeringDrafting(true);
      const res = await triggerDrafting();
      showNotification(res.message || 'Drafting batch sweep queued!');
      setTimeout(loadData, 2000);
    } catch (err: any) {
      showNotification(`Drafting trigger error: ${err.message}`, 'error');
    } finally {
      setTriggeringDrafting(false);
    }
  };

  const copyDraft = (draft: any, id: string) => {
    if (!draft) return;
    const text = `Subject: ${draft.subject_line}\n\n${draft.body}`;
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  return (
    <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '2rem 1.5rem' }}>
      {/* Toast Notification */}
      {notification && (
        <div
          style={{
            position: 'fixed',
            bottom: '24px',
            right: '24px',
            zIndex: 100,
            padding: '12px 20px',
            borderRadius: '12px',
            backgroundColor: notification.type === 'success' ? '#065f46' : '#991b1b',
            color: '#ffffff',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            boxShadow: '0 10px 25px rgba(0,0,0,0.5)',
            border: '1px solid rgba(255,255,255,0.2)',
            animation: 'fadeIn 0.2s ease-in-out',
          }}
        >
          {notification.type === 'success' ? <CheckCircle size={18} /> : <AlertCircle size={18} />}
          <span style={{ fontSize: '0.9rem', fontWeight: 500 }}>{notification.message}</span>
        </div>
      )}

      {/* Header */}
      <header
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '1.5rem',
          marginBottom: '2rem',
          paddingBottom: '1.5rem',
          borderBottom: '1px solid var(--border-color)',
        }}
      >
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div
              style={{
                width: '36px',
                height: '36px',
                borderRadius: '10px',
                background: 'var(--accent-gradient)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Zap size={20} color="#fff" />
            </div>
            <h1 style={{ fontSize: '1.75rem', fontWeight: 800, letterSpacing: '-0.02em' }}>
              Cold Email Agent
            </h1>
            <span
              style={{
                fontSize: '0.75rem',
                padding: '3px 8px',
                borderRadius: '999px',
                backgroundColor: 'rgba(99, 102, 241, 0.15)',
                color: '#818cf8',
                border: '1px solid rgba(99, 102, 241, 0.3)',
                fontWeight: 600,
              }}
            >
              Live Pipeline
            </span>
          </div>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: '0.35rem' }}>
            Autonomous startup discovery, deep research, personalized draft synthesis, and dispatch.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '10px' }}>
          <button
            onClick={handleTriggerDiscovery}
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
            onClick={handleTriggerDrafting}
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

          <button
            onClick={loadData}
            title="Refresh data"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '42px',
              height: '42px',
              borderRadius: '10px',
              backgroundColor: 'var(--bg-secondary)',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-color)',
              cursor: 'pointer',
            }}
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </header>

      {/* Pipeline Stats Bar */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
          gap: '12px',
          marginBottom: '2rem',
        }}
      >
        {[
          { label: 'Total Leads', count: stats?.total ?? 0, color: '#f8fafc', bg: 'var(--bg-secondary)' },
          { label: 'Found', count: stats?.found ?? 0, color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.08)' },
          { label: 'Researched', count: stats?.researched ?? 0, color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.08)' },
          { label: 'Review Queue', count: stats?.drafted ?? 0, color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.12)', highlight: true },
          { label: 'Approved', count: stats?.approved ?? 0, color: '#34d399', bg: 'rgba(52, 211, 153, 0.08)' },
          { label: 'Sent', count: stats?.sent ?? 0, color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.08)' },
          { label: 'Rejected', count: stats?.rejected ?? 0, color: '#f87171', bg: 'rgba(248, 113, 113, 0.08)' },
        ].map((item, idx) => (
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

      {/* Tabs */}
      <div
        style={{
          display: 'flex',
          gap: '8px',
          borderBottom: '1px solid var(--border-color)',
          marginBottom: '1.5rem',
        }}
      >
        <button
          onClick={() => setActiveTab('review')}
          style={{
            padding: '10px 20px',
            border: 'none',
            background: 'none',
            color: activeTab === 'review' ? 'var(--text-primary)' : 'var(--text-secondary)',
            fontWeight: 700,
            fontSize: '0.95rem',
            cursor: 'pointer',
            borderBottom: activeTab === 'review' ? '2px solid var(--accent-primary)' : '2px solid transparent',
            marginBottom: '-1px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <Mail size={16} />
          Draft Review Queue
          {draftQueue.length > 0 && (
            <span
              style={{
                fontSize: '0.75rem',
                backgroundColor: 'var(--warning)',
                color: '#000',
                padding: '2px 8px',
                borderRadius: '999px',
                fontWeight: 800,
              }}
            >
              {draftQueue.length}
            </span>
          )}
        </button>

        <button
          onClick={() => setActiveTab('explorer')}
          style={{
            padding: '10px 20px',
            border: 'none',
            background: 'none',
            color: activeTab === 'explorer' ? 'var(--text-primary)' : 'var(--text-secondary)',
            fontWeight: 700,
            fontSize: '0.95rem',
            cursor: 'pointer',
            borderBottom: activeTab === 'explorer' ? '2px solid var(--accent-primary)' : '2px solid transparent',
            marginBottom: '-1px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <Building size={16} />
          All Leads Explorer
        </button>
      </div>

      {/* Tab Content: Draft Review Queue */}
      {activeTab === 'review' && (
        <div>
          {loading ? (
            <div style={{ textAlign: 'center', padding: '4rem', color: 'var(--text-muted)' }}>
              <RefreshCw size={24} style={{ animation: 'spin 1s linear infinite' }} />
              <p style={{ marginTop: '1rem' }}>Loading review queue...</p>
            </div>
          ) : draftQueue.length === 0 ? (
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
                <button
                  onClick={handleTriggerDiscovery}
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
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
              {draftQueue.map((lead) => (
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
                        onClick={() => handleApprove(lead)}
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
                        onClick={() => handleRegenerate(lead)}
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
        </div>
      )}

      {/* Tab Content: All Leads Explorer */}
      {activeTab === 'explorer' && (
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
              {['all', 'found', 'researched', 'drafted', 'approved', 'sent', 'rejected', 'failed'].map((st) => (
                <button
                  key={st}
                  onClick={() => setStatusFilter(st)}
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
                onChange={(e) => setSearchQuery(e.target.value)}
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
                {allLeads.map((lead) => (
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
