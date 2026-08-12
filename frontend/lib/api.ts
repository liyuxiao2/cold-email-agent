export interface LeadDraft {
  id: string;
  subject_line: string;
  body: string;
  version: number;
  gmail_draft_id?: string | null;
  created_at?: string | null;
}

export interface LeadResearch {
  hook?: string | null;
  tech_stack?: any;
  recent_news?: string | null;
}

export interface LeadItem {
  id: string;
  company_name: string;
  founder_name?: string | null;
  founder_email?: string | null;
  company_url?: string | null;
  linkedin_url?: string | null;
  funding_stage?: string | null;
  headcount?: number | null;
  status: string;
  error_msg?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  draft?: LeadDraft | null;
  research?: LeadResearch | null;
}

export interface PipelineStats {
  total: number;
  found: number;
  researched: number;
  drafted: number;
  approved: number;
  sent: number;
  rejected: number;
  failed: number;
}

const getApiBaseUrl = () => {
  if (typeof window !== 'undefined') {
    return process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  }
  return process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
};

export async function fetchPipelineStats(): Promise<PipelineStats> {
  const res = await fetch(`${getApiBaseUrl()}/api/pipeline/stats`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch stats: ${res.statusText}`);
  return res.json();
}

export async function fetchDraftQueue(): Promise<LeadItem[]> {
  const res = await fetch(`${getApiBaseUrl()}/api/leads/drafts`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch draft queue: ${res.statusText}`);
  return res.json();
}

export async function fetchLeads(params?: { status?: string; search?: string; limit?: number; offset?: number }) {
  const query = new URLSearchParams();
  if (params?.status) query.set('status', params.status);
  if (params?.search) query.set('search', params.search);
  if (params?.limit) query.set('limit', params.limit.toString());
  if (params?.offset) query.set('offset', params.offset.toString());

  const res = await fetch(`${getApiBaseUrl()}/api/leads?${query.toString()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to fetch leads: ${res.statusText}`);
  return res.json() as Promise<{ items: LeadItem[]; total: number; limit: number; offset: number }>;
}

export async function approveLead(leadId: string) {
  const res = await fetch(`${getApiBaseUrl()}/api/leads/${leadId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`Failed to approve lead: ${res.statusText}`);
  return res.json();
}

export async function rejectLead(leadId: string, notes: string = '') {
  const res = await fetch(`${getApiBaseUrl()}/api/leads/${leadId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ notes }),
  });
  if (!res.ok) throw new Error(`Failed to reject lead: ${res.statusText}`);
  return res.json();
}

export async function regenerateDraft(leadId: string) {
  const res = await fetch(`${getApiBaseUrl()}/api/leads/${leadId}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`Failed to regenerate draft: ${res.statusText}`);
  return res.json();
}

export async function triggerDiscovery() {
  const res = await fetch(`${getApiBaseUrl()}/api/pipeline/discovery`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`Failed to trigger discovery: ${res.statusText}`);
  return res.json();
}

export async function triggerDrafting() {
  const res = await fetch(`${getApiBaseUrl()}/api/pipeline/drafting`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`Failed to trigger drafting: ${res.statusText}`);
  return res.json();
}
