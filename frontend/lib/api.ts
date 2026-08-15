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

export interface CompanyItem {
  id: string;
  company_name: string;
  founder_name?: string | null;
  company_url?: string | null;
  linkedin_url?: string | null;
  funding_stage?: string | null;
  headcount?: number | null;
  industry?: string | null;
  research_status: string;
  error_msg?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  research?: OutreachResearch | null;
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

export interface CompanyPage {
  items: CompanyItem[];
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

export function fetchCompanies(params?: {
  status?: string;
  search?: string;
  limit?: number;
  offset?: number;
}): Promise<CompanyPage> {
  const query = new URLSearchParams();
  if (params?.status) query.set('status', params.status);
  if (params?.search) query.set('search', params.search);
  if (params?.limit) query.set('limit', params.limit.toString());
  if (params?.offset) query.set('offset', params.offset.toString());

  return request<CompanyPage>(`/api/companies?${query.toString()}`);
}

export function approveOutreach(outreachId: string): Promise<TaskAck> {
  return request<TaskAck>(`/api/outreach/${outreachId}/approve`, { method: 'POST' });
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
