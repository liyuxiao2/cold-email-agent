export interface OutreachDraft {
  id: string;
  subject_line: string;
  body: string;
  version: number;
  gmail_draft_id?: string | null;
  created_at?: string | null;
}

export interface OutreachResearch {
  hook?: string | null;
  tech_stack?: any;
  recent_news?: string | null;
}

export interface OutreachCompany {
  id: string;
  company_name: string;
  company_url?: string | null;
  linkedin_url?: string | null;
  funding_stage?: string | null;
  headcount?: number | null;
}

export interface OutreachContact {
  first_name?: string | null;
  position?: string | null;
  email?: string | null;
}

export interface OutreachItem {
  outreach_id: string;
  status: string;
  company: OutreachCompany | null;
  contact: OutreachContact | null;
  draft?: OutreachDraft | null;
  research?: OutreachResearch | null;
  error_msg?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface PipelineStats {
  companies: Record<string, number>;
  outreach: Record<string, number>;
}

export interface TaskAck {
  success: boolean;
  message?: string;
  task_id?: string;
}

export type SenderProfile = {
  name: string;
  intro: string;
  linkedin: string | null;
  github: string | null;
  website: string | null;
  experience_pool: string[];
  company_links: Record<string, string>;
  has_resume: boolean;
  resume_filename: string | null;
};

export interface ResumeUploadResult {
  stored: boolean;
  /** A SUGGESTED profile only — nothing is saved until PUT /api/profile. */
  suggested: Partial<SenderProfile>;
}

export interface OutreachPage {
  items: OutreachItem[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * One company from the SHARED pool a user can still target.
 *
 * `contact_count` and `has_founder_contact` are the only availability
 * signals — GET /api/companies deliberately never returns an email address
 * (see cold_email/api/routes/companies.py's module docstring). Do not add a
 * field here that implies an address is visible before POST /api/outreach
 * assigns the caller a contact.
 */
export type PoolCompany = {
  id: string;
  company_name: string;
  company_url: string | null;
  linkedin_url: string | null;
  founder_name: string | null;
  funding_stage: string | null;
  headcount: number | null;
  industry: string | null;
  contact_count: number;
  has_founder_contact: boolean;
  research: { hook: string | null; tech_stack: string[] | null };
};

export interface PoolPage {
  items: PoolCompany[];
  total: number;
  limit: number;
  offset: number;
}

export type SkippedOutreach = { company_id: string; reason: string };

export type CreateOutreachResult = {
  created: { outreach_id: string; company_id: string; contact_id: string }[];
  skipped: SkippedOutreach[];
  quota: { used: number; limit: number };
};

export type QuotaStatus = { used: number; limit: number; period_end: string };

export type LlmKeyStatus = {
  provider: 'groq' | 'gemini' | null;
  configured: boolean;
  last4: string | null;
};

/** The JSONB shape stored on users.send_cadence. See cold_email/cadence.py. */
export type Cadence = {
  max_per_day: number;
  days: number[];
  window_start: string;
  window_end: string;
  timezone: string;
};

export type ApproveResult = {
  success: boolean;
  outreach_id: string;
  status: string;
  scheduled_send_at: string | null;
};

export type BulkApproveResult = {
  approved: { outreach_id: string; scheduled_send_at: string | null }[];
  skipped: number;
};

export type ScheduledItem = {
  outreach_id: string;
  company_name: string;
  scheduled_send_at: string;
};

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
  const method = (init?.method ?? 'GET').toUpperCase();

  // Simple GET/HEAD requests with no custom header are not CORS-preflighted;
  // adding Content-Type promotes them into preflighted ones, adding an extra
  // OPTIONS round-trip to every fetch. Every other method — INCLUDING the
  // several intentionally bodyless POSTs here (approve, regenerate,
  // discovery, drafting) — keeps sending it: a planned follow-up may rely on
  // "requires Content-Type: application/json" as a CSRF defence, so do not
  // "simplify" this to "only when a body is present".
  const headers =
    method === 'GET' || method === 'HEAD'
      ? { ...init?.headers }
      : { 'Content-Type': 'application/json', ...init?.headers };

  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    cache: 'no-store',
    credentials: 'include', // required: cookies are not sent cross-origin by default
    headers,
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

export function fetchDraftQueue(): Promise<OutreachItem[]> {
  return request<OutreachItem[]>('/api/outreach/drafts');
}

/**
 * Approve a draft, optionally scheduling it.
 *
 * With no body: sends on the caller's next free cadence slot if one is
 * configured, otherwise on the next scanner tick (<=5 min). `scheduled_send_at`
 * pins an exact instant (even one in the past -- that reads as "send now").
 * `send_now: true` overrides any cadence and sends on the next tick.
 */
export function approveOutreach(
  outreachId: string,
  body?: { scheduled_send_at?: string; send_now?: boolean }
): Promise<ApproveResult> {
  return request<ApproveResult>(`/api/outreach/${outreachId}/approve`, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  });
}

/** Approves many drafts at once, spreading them across cadence slots in ONE
 * walk -- approving each individually would give every draft the same next
 * free slot. */
export function bulkApprove(outreachIds: string[], sendNow = false): Promise<BulkApproveResult> {
  return request<BulkApproveResult>('/api/outreach/bulk-approve', {
    method: 'POST',
    body: JSON.stringify({ outreach_ids: outreachIds, send_now: sendNow }),
  });
}

/** Pulls an approved send back to the review deck, as 'drafted' (the draft
 * already exists -- no LLM re-run). */
export function unscheduleOutreach(outreachId: string): Promise<{ success: boolean; status: string }> {
  return request<{ success: boolean; status: string }>(`/api/outreach/${outreachId}/unschedule`, {
    method: 'POST',
  });
}

/** The caller's own upcoming sends, soonest first. */
export function getScheduled(): Promise<{ items: ScheduledItem[] }> {
  return request<{ items: ScheduledItem[] }>('/api/outreach/scheduled');
}

export function getCadence(): Promise<{ cadence: Cadence | null }> {
  return request<{ cadence: Cadence | null }>('/api/cadence');
}

export function putCadence(cadence: Cadence): Promise<{ cadence: Cadence }> {
  return request<{ cadence: Cadence }>('/api/cadence', {
    method: 'PUT',
    body: JSON.stringify(cadence),
  });
}

export function deleteCadence(): Promise<{ cadence: null }> {
  return request<{ cadence: null }>('/api/cadence', { method: 'DELETE' });
}

export function rejectOutreach(outreachId: string, notes: string = ''): Promise<TaskAck> {
  return request<TaskAck>(`/api/outreach/${outreachId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ notes }),
  });
}

export function regenerateDraft(outreachId: string): Promise<TaskAck> {
  return request<TaskAck>(`/api/outreach/${outreachId}/regenerate`, { method: 'POST' });
}

export function triggerDiscovery(): Promise<TaskAck> {
  return request<TaskAck>('/api/pipeline/discovery', { method: 'POST' });
}

export function triggerDrafting(): Promise<TaskAck> {
  return request<TaskAck>('/api/pipeline/drafting', { method: 'POST' });
}

/** The global company pool, filtered and with the caller's already-targeted
 * companies excluded server-side (see companies.py). */
export function getPool(params?: {
  industry?: string;
  fundingStage?: string;
  headcountMin?: number;
  headcountMax?: number;
  search?: string;
  hasFounderContact?: boolean;
  limit?: number;
  offset?: number;
}): Promise<PoolPage> {
  const query = new URLSearchParams();
  if (params?.industry) query.set('industry', params.industry);
  if (params?.fundingStage) query.set('funding_stage', params.fundingStage);
  if (params?.headcountMin !== undefined) query.set('headcount_min', String(params.headcountMin));
  if (params?.headcountMax !== undefined) query.set('headcount_max', String(params.headcountMax));
  if (params?.search) query.set('search', params.search);
  if (params?.hasFounderContact) query.set('has_founder_contact', 'true');
  query.set('limit', String(params?.limit ?? 50));
  query.set('offset', String(params?.offset ?? 0));
  return request<PoolPage>(`/api/companies?${query.toString()}`);
}

/** Queue drafts for the selected companies. PARTIAL SUCCESS: some ids may be
 * skipped (already targeted, exhausted, over quota) while the rest queue. */
export function createOutreach(companyIds: string[]): Promise<CreateOutreachResult> {
  return request<CreateOutreachResult>('/api/outreach', {
    method: 'POST',
    body: JSON.stringify({ company_ids: companyIds }),
  });
}

export function getQuota(): Promise<QuotaStatus> {
  return request<QuotaStatus>('/api/quota');
}

export function getLlmKey(): Promise<LlmKeyStatus> {
  return request<LlmKeyStatus>('/api/llm-key');
}

/** Validated server-side with one live call before it's stored — an invalid
 * key would otherwise fail every draft one at a time inside a Celery worker. */
export function setLlmKey(provider: 'groq' | 'gemini', apiKey: string): Promise<TaskAck> {
  return request<TaskAck>('/api/llm-key', {
    method: 'PUT',
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
}

export function deleteLlmKey(): Promise<TaskAck> {
  return request<TaskAck>('/api/llm-key', { method: 'DELETE' });
}

export function getProfile(): Promise<SenderProfile> {
  return request<SenderProfile>('/api/profile');
}

export function saveProfile(profile: Partial<SenderProfile>): Promise<SenderProfile> {
  return request<SenderProfile>('/api/profile', { method: 'PUT', body: JSON.stringify(profile) });
}

/**
 * Uploads a résumé PDF and returns a SUGGESTED profile for the user to review
 * before saving — it never writes to the profile itself.
 *
 * Deliberately bypasses `request()`: that helper sets `Content-Type:
 * application/json` on every non-GET call, which would stomp the
 * `multipart/form-data; boundary=...` header the browser needs to set itself
 * from the FormData body. A hand-set multipart Content-Type (missing the
 * boundary) makes the server unable to parse the body at all.
 */
export async function uploadResume(file: File): Promise<ResumeUploadResult> {
  const form = new FormData();
  form.append('file', file);
  const response = await fetch(`${getApiBaseUrl()}/api/profile/resume`, {
    method: 'POST',
    credentials: 'include', // required: cookies are not sent cross-origin by default
    body: form,
  });
  if (response.status === 401 && typeof window !== 'undefined') {
    window.location.href = '/login';
    throw new Error('Not authenticated');
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail ?? `${response.status} upload failed`);
  }
  return response.json() as Promise<ResumeUploadResult>;
}

export function deleteResume(): Promise<TaskAck> {
  return request<TaskAck>('/api/profile/resume', { method: 'DELETE' });
}

/** Kicks off the same Google OAuth consent flow used at sign-in. Google only
 * returns a refresh token on a consent screen, so re-running this — not some
 * separate "reconnect" endpoint — is how a user with a lapsed/missing Gmail
 * token gets one again. */
export async function startGoogleLogin(): Promise<void> {
  const response = await fetch(`${getApiBaseUrl()}/api/auth/google/login`, {
    credentials: 'include',
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const { authorize_url } = (await response.json()) as { authorize_url: string };
  window.location.href = authorize_url;
}
