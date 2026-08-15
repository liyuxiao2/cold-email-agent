'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  CheckCircle,
  RefreshCw,
  Zap,
  Compass,
  Clock,
  Mail,
  LogOut,
  AlertCircle,
  User as UserIcon,
} from 'lucide-react';
import {
  Cadence,
  OutreachItem,
  PipelineStats as PipelineStatsData,
  ScheduledItem,
  approveOutreach,
  deleteCadence,
  fetchDraftQueue,
  fetchPipelineStats,
  getCadence,
  getScheduled,
  putCadence,
  regenerateDraft,
  rejectOutreach,
  triggerDiscovery,
  triggerDrafting,
  unscheduleOutreach,
} from '@/lib/api';
import { useAuth } from '@/lib/auth';
import AdminPanel from '@/components/AdminPanel';
import CadenceSettings from '@/components/CadenceSettings';
import PipelineStats from '@/components/PipelineStats';
import ReviewDeck from '@/components/ReviewDeck';
import ScheduledQueue from '@/components/ScheduledQueue';

const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err));

/**
 * The dashboard container: auth gate, all shared state, all data fetching.
 *
 * Everything visual lives in the presentational components below. The old
 * "Company Explorer" tab (a stale-shape table over /api/companies) was
 * retired in favor of the dedicated /pool page, which already covers browsing
 * the shared company pool against the real PoolCompany shape.
 */
export default function DashboardPage() {
  const { user, loading: authLoading, logout } = useAuth();
  const router = useRouter();

  const [stats, setStats] = useState<PipelineStatsData | null>(null);
  const [draftQueue, setDraftQueue] = useState<OutreachItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [triggeringDiscovery, setTriggeringDiscovery] = useState<boolean>(false);
  const [triggeringDrafting, setTriggeringDrafting] = useState<boolean>(false);
  const [notification, setNotification] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

  const [cadence, setCadence] = useState<Cadence | null>(null);
  const [cadenceSaving, setCadenceSaving] = useState<boolean>(false);
  const [scheduled, setScheduled] = useState<ScheduledItem[]>([]);
  const [scheduledLoading, setScheduledLoading] = useState<boolean>(true);

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

  const loadScheduling = useCallback(async () => {
    try {
      setScheduledLoading(true);
      const [cadenceData, scheduledData] = await Promise.all([getCadence(), getScheduled()]);
      setCadence(cadenceData.cadence);
      setScheduled(scheduledData.items);
    } catch (err: unknown) {
      console.error(err);
      showNotification(`Connection error: ${errorMessage(err)}`, 'error');
    } finally {
      setScheduledLoading(false);
    }
  }, [showNotification]);

  /** Renders how the send time is displayed for the toast and the scheduled
   * queue: the cadence's own timezone if one is configured, otherwise the
   * browser's -- always with a UTC offset alongside so a bare local time is
   * never ambiguous. */
  const formatInDisplayTz = (iso: string) =>
    new Intl.DateTimeFormat('en-US', {
      timeZone: cadence?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
      weekday: 'short',
      hour: 'numeric',
      minute: '2-digit',
    }).format(new Date(iso));

  useEffect(() => {
    if (authLoading) return;
    if (!user) router.push('/login');
    else if (!user.profile_complete) router.push('/onboarding');
  }, [authLoading, user, router]);

  // Only fetch once we know there is a session; an anonymous call would 401.
  useEffect(() => {
    if (user) {
      loadData();
      loadScheduling();
    }
  }, [user, loadData, loadScheduling]);

  /** Bumps the per-user `outreach` half of stats; `companies` is untouched —
      these actions never change the global research pool. */
  const bumpOutreachStats = (from: string, to: string) => {
    if (!stats) return;
    setStats({
      ...stats,
      outreach: {
        ...stats.outreach,
        [from]: Math.max(0, (stats.outreach[from] ?? 0) - 1),
        [to]: (stats.outreach[to] ?? 0) + 1,
      },
    });
  };

  /** Common-case approve: uses the caller's cadence if one is configured,
   * otherwise sends on the next scanner tick (<=5 min). Approve only sets
   * state now -- send_due_task's Beat scan does the actual dispatch, so the
   * toast reports what WILL happen, not "dispatched". */
  const handleApprove = async (lead: OutreachItem) => {
    const name = lead.company?.company_name ?? 'company';
    try {
      setActionLoading(lead.outreach_id);
      const result = await approveOutreach(lead.outreach_id);
      setDraftQueue((prev) => prev.filter((item) => item.outreach_id !== lead.outreach_id));
      bumpOutreachStats('drafted', 'approved');
      showNotification(
        result.scheduled_send_at
          ? `Approved ${name} -- scheduled for ${formatInDisplayTz(result.scheduled_send_at)}.`
          : `Approved ${name} -- sending within 5 minutes.`
      );
      loadScheduling();
    } catch (err: unknown) {
      showNotification(`Failed to approve: ${errorMessage(err)}`, 'error');
    } finally {
      setActionLoading(null);
    }
  };

  /** The "Approve & schedule…" dialog's confirm. `whenIso` is null for "send
   * now" (overrides any cadence). Returns whether it succeeded so the dialog
   * only closes on success. */
  const handleApproveScheduled = async (lead: OutreachItem, whenIso: string | null): Promise<boolean> => {
    const name = lead.company?.company_name ?? 'company';
    try {
      setActionLoading(lead.outreach_id);
      const result = whenIso
        ? await approveOutreach(lead.outreach_id, { scheduled_send_at: whenIso })
        : await approveOutreach(lead.outreach_id, { send_now: true });
      setDraftQueue((prev) => prev.filter((item) => item.outreach_id !== lead.outreach_id));
      bumpOutreachStats('drafted', 'approved');
      showNotification(
        result.scheduled_send_at
          ? `Approved ${name} -- scheduled for ${formatInDisplayTz(result.scheduled_send_at)}.`
          : `Approved ${name} -- sending within 5 minutes.`
      );
      loadScheduling();
      return true;
    } catch (err: unknown) {
      showNotification(`Failed to schedule: ${errorMessage(err)}`, 'error');
      return false;
    } finally {
      setActionLoading(null);
    }
  };

  const handleSaveCadence = async (next: Cadence) => {
    try {
      setCadenceSaving(true);
      const result = await putCadence(next);
      setCadence(result.cadence);
      showNotification('Cadence saved.');
    } catch (err: unknown) {
      showNotification(`Failed to save cadence: ${errorMessage(err)}`, 'error');
    } finally {
      setCadenceSaving(false);
    }
  };

  const handleClearCadence = async () => {
    try {
      setCadenceSaving(true);
      await deleteCadence();
      setCadence(null);
      showNotification('Cadence cleared -- approvals send immediately.');
    } catch (err: unknown) {
      showNotification(`Failed to clear cadence: ${errorMessage(err)}`, 'error');
    } finally {
      setCadenceSaving(false);
    }
  };

  const handleUnschedule = async (outreachId: string) => {
    try {
      setActionLoading(outreachId);
      await unscheduleOutreach(outreachId);
      setScheduled((prev) => prev.filter((item) => item.outreach_id !== outreachId));
      bumpOutreachStats('approved', 'drafted');
      showNotification('Unscheduled -- back in the review queue.');
      loadData();
    } catch (err: unknown) {
      showNotification(`Failed to unschedule: ${errorMessage(err)}`, 'error');
    } finally {
      setActionLoading(null);
    }
  };

  /** Returns true on success so ReviewDeck knows whether to close its modal. */
  const handleReject = async (outreachId: string, notes: string): Promise<boolean> => {
    try {
      setActionLoading(outreachId);
      await rejectOutreach(outreachId, notes);
      setDraftQueue((prev) => prev.filter((item) => item.outreach_id !== outreachId));
      bumpOutreachStats('drafted', 'rejected');
      showNotification(`Rejected lead.`);
      return true;
    } catch (err: unknown) {
      showNotification(`Failed to reject: ${errorMessage(err)}`, 'error');
      return false;
    } finally {
      setActionLoading(null);
    }
  };

  const handleRegenerate = async (lead: OutreachItem) => {
    try {
      setActionLoading(lead.outreach_id);
      await regenerateDraft(lead.outreach_id);
      setDraftQueue((prev) => prev.filter((item) => item.outreach_id !== lead.outreach_id));
      bumpOutreachStats('drafted', 'queued');
      showNotification(`Draft queued for re-generation (${lead.company?.company_name ?? 'company'})`);
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
  if (!user || !user.profile_complete) return null;

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
            onClick={() => router.push('/pool')}
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
              cursor: 'pointer',
            }}
          >
            <Compass size={16} color="#818cf8" />
            Browse Pool
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
              onClick={() => router.push('/profile')}
              title={user.gmail_connected ? 'Your profile' : 'Your profile (Gmail not connected)'}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: '42px',
                height: '42px',
                borderRadius: '10px',
                backgroundColor: 'var(--bg-secondary)',
                color: user.gmail_connected ? 'var(--text-secondary)' : 'var(--warning)',
                border: `1px solid ${user.gmail_connected ? 'var(--border-color)' : 'var(--warning)'}`,
                cursor: 'pointer',
              }}
            >
              <UserIcon size={16} />
            </button>
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

      {/* Draft Review Queue */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '10px 0 1.5rem',
          fontWeight: 700,
          fontSize: '0.95rem',
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
      </div>

      <ReviewDeck
        leads={draftQueue}
        loading={loading}
        actionLoading={actionLoading}
        onApprove={handleApprove}
        onApproveScheduled={handleApproveScheduled}
        onReject={handleReject}
        onRegenerate={handleRegenerate}
        onTriggerDiscovery={handleTriggerDiscovery}
        isAdmin={user.role === 'admin'}
      />

      {/* Scheduling */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '2.5rem 0 1.5rem',
          fontWeight: 700,
          fontSize: '0.95rem',
        }}
      >
        <Clock size={16} />
        Scheduling
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(280px, 1fr) minmax(320px, 1.2fr)',
          gap: '1.5rem',
        }}
      >
        <CadenceSettings
          cadence={cadence}
          saving={cadenceSaving}
          onSave={handleSaveCadence}
          onClear={handleClearCadence}
        />

        <div
          style={{
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            borderRadius: '16px',
            padding: '1.5rem',
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: '0.75rem' }}>Upcoming sends</div>
          <ScheduledQueue
            items={scheduled}
            loading={scheduledLoading}
            displayTimezone={cadence?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone}
            actionLoading={actionLoading}
            onUnschedule={handleUnschedule}
          />
        </div>
      </div>
    </div>
  );
}
