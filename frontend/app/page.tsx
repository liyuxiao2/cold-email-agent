'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  CheckCircle,
  RefreshCw,
  Zap,
  Building,
  Mail,
  LogOut,
  AlertCircle,
} from 'lucide-react';
import {
  LeadItem,
  PipelineStats as PipelineStatsData,
  approveLead,
  fetchDraftQueue,
  fetchLeads,
  fetchPipelineStats,
  regenerateDraft,
  rejectLead,
  triggerDiscovery,
  triggerDrafting,
} from '@/lib/api';
import { useAuth } from '@/lib/auth';
import AdminPanel from '@/components/AdminPanel';
import LeadExplorer from '@/components/LeadExplorer';
import PipelineStats from '@/components/PipelineStats';
import ReviewDeck from '@/components/ReviewDeck';

const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * The dashboard container: auth gate, all shared state, all data fetching.
 *
 * Everything visual lives in the four presentational components below. State
 * that only one of them reads (copy-button feedback, reject-modal text, the
 * explorer's filters) lives inside that component instead of here.
 */
export default function DashboardPage() {
  const { user, loading: authLoading, logout } = useAuth();
  const router = useRouter();

  const [stats, setStats] = useState<PipelineStatsData | null>(null);
  const [draftQueue, setDraftQueue] = useState<LeadItem[]>([]);
  const [allLeads, setAllLeads] = useState<LeadItem[]>([]);
  const [activeTab, setActiveTab] = useState<'review' | 'explorer'>('review');
  const [loading, setLoading] = useState<boolean>(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [triggeringDiscovery, setTriggeringDiscovery] = useState<boolean>(false);
  const [triggeringDrafting, setTriggeringDrafting] = useState<boolean>(false);
  const [notification, setNotification] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

  const showNotification = useCallback((message: string, type: 'success' | 'error' = 'success') => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 4000);
  }, []);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [statsData, queueData] = await Promise.all([
        fetchPipelineStats(),
        fetchDraftQueue(),
      ]);
      setStats(statsData);
      setDraftQueue(queueData);
    } catch (err: unknown) {
      console.error(err);
      showNotification(`Connection error: ${errorMessage(err)}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [showNotification]);

  // Called by LeadExplorer on mount and on every filter change.
  const loadExplorerLeads = useCallback(async (status: string, search: string) => {
    try {
      const filter = status === 'all' ? undefined : status;
      const data = await fetchLeads({ status: filter, search: search || undefined, limit: 100 });
      setAllLeads(data.items);
    } catch (err: unknown) {
      console.error(err);
    }
  }, []);

  useEffect(() => {
    if (!authLoading && !user) router.push('/login');
  }, [authLoading, user, router]);

  // Only fetch once we know there is a session; an anonymous call would 401.
  useEffect(() => {
    if (user) loadData();
  }, [user, loadData]);

  const handleApprove = async (lead: LeadItem) => {
    try {
      setActionLoading(lead.id);
      await approveLead(lead.id);
      setDraftQueue((prev) => prev.filter((item) => item.id !== lead.id));
      if (stats) setStats({ ...stats, drafted: Math.max(0, stats.drafted - 1), approved: stats.approved + 1 });
      showNotification(`Approved outreach for ${lead.company_name}! Dispatched logistics task.`);
    } catch (err: unknown) {
      showNotification(`Failed to approve: ${errorMessage(err)}`, 'error');
    } finally {
      setActionLoading(null);
    }
  };

  /** Returns true on success so ReviewDeck knows whether to close its modal. */
  const handleReject = async (leadId: string, notes: string): Promise<boolean> => {
    try {
      setActionLoading(leadId);
      await rejectLead(leadId, notes);
      setDraftQueue((prev) => prev.filter((item) => item.id !== leadId));
      if (stats) setStats({ ...stats, drafted: Math.max(0, stats.drafted - 1), rejected: stats.rejected + 1 });
      showNotification(`Rejected lead.`);
      return true;
    } catch (err: unknown) {
      showNotification(`Failed to reject: ${errorMessage(err)}`, 'error');
      return false;
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
    } catch (err: unknown) {
      showNotification(`Failed to regenerate: ${errorMessage(err)}`, 'error');
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
    } catch (err: unknown) {
      showNotification(`Discovery trigger error: ${errorMessage(err)}`, 'error');
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
    } catch (err: unknown) {
      showNotification(`Drafting trigger error: ${errorMessage(err)}`, 'error');
    } finally {
      setTriggeringDrafting(false);
    }
  };

  if (authLoading) {
    return (
      <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '2rem 1.5rem', color: 'var(--text-muted)' }}>
        Loading…
      </div>
    );
  }
  if (!user) return null;

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

        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
          {/* Cosmetic only. `require_admin` on POST /api/pipeline/* is the real
              boundary — hiding a button is not authorization. */}
          {user.role === 'admin' && (
            <AdminPanel
              triggeringDiscovery={triggeringDiscovery}
              triggeringDrafting={triggeringDrafting}
              onTriggerDiscovery={handleTriggerDiscovery}
              onTriggerDrafting={handleTriggerDrafting}
            />
          )}

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

          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              paddingLeft: '10px',
              borderLeft: '1px solid var(--border-color)',
            }}
          >
            <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{user.email}</span>
            <button
              onClick={logout}
              title="Sign out"
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
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </header>

      {/* Pipeline Stats Bar */}
      <PipelineStats stats={stats} loading={loading} />

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
        <ReviewDeck
          leads={draftQueue}
          loading={loading}
          actionLoading={actionLoading}
          onApprove={handleApprove}
          onReject={handleReject}
          onRegenerate={handleRegenerate}
          onTriggerDiscovery={handleTriggerDiscovery}
        />
      )}

      {/* Tab Content: All Leads Explorer */}
      {activeTab === 'explorer' && (
        <LeadExplorer leads={allLeads} onFiltersChange={loadExplorerLeads} />
      )}
    </div>
  );
}
