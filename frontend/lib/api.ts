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

export interface TaskAck {
  success: boolean;
  message?: string;
  task_id?: string;
}

export interface LeadPage {
  items: LeadItem[];
  total: number;
  limit: number;
  offset: number;
}

const getApiBaseUrl = () => {
  if (typeof window !== 'undefined') {
    return process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  }
  return process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
};

/**
 * The single door every backend call goes through.
 *
 * Two things are easy to forget at a call site and fatal when forgotten, so
 * they live here instead: `credentials: 'include'` (the session cookie is not
 * sent cross-origin without it, so every request would arrive anonymous) and
 * the 401 -> /login redirect.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    cache: 'no-store',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });

  if (response.status === 401 && typeof window !== 'undefined') {
    window.location.href = '/login';
    throw new Error('Not authenticated');
  }
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

export function fetchPipelineStats(): Promise<PipelineStats> {
  return request<PipelineStats>('/api/pipeline/stats');
}

export function fetchDraftQueue(): Promise<LeadItem[]> {
  return request<LeadItem[]>('/api/leads/drafts');
}

export function fetchLeads(params?: {
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<LeadPage> {
  const query = new URLSearchParams();
  if (params?.status) query.set('status', params.status);
  if (params?.search) query.set('search', params.search);
  if (params?.limit) query.set('limit', params.limit.toString());
  if (params?.offset) query.set('offset', params.offset.toString());

  return request<LeadPage>(`/api/leads?${query.toString()}`);
}

export function approveLead(leadId: string): Promise<TaskAck> {
  return request<TaskAck>(`/api/leads/${leadId}/approve`, { method: 'POST' });
}

export function rejectLead(leadId: string, notes: string = ''): Promise<TaskAck> {
  return request<TaskAck>(`/api/leads/${leadId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ notes }),
  });
}

export function regenerateDraft(leadId: string): Promise<TaskAck> {
  return request<TaskAck>(`/api/leads/${leadId}/regenerate`, { method: 'POST' });
}

export function triggerDiscovery(): Promise<TaskAck> {
  return request<TaskAck>('/api/pipeline/discovery', { method: 'POST' });
}

export function triggerDrafting(): Promise<TaskAck> {
  return request<TaskAck>('/api/pipeline/drafting', { method: 'POST' });
}
