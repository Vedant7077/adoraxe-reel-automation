/**
 * API client – all calls to the FastAPI backend.
 * Never exposes credentials. Reads BACKEND_URL from env.
 */

const API_BASE = import.meta.env.VITE_BACKEND_URL || (import.meta.env as any).PUBLIC_API_URL || 'http://localhost:8000';
const API_URL = `${API_BASE}/api/v1`;

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_URL}${path}`;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };

  // Attach X-Admin-Key from sessionStorage (browser-only, SSR-safe)
  if (typeof window !== 'undefined') {
    const adminKey = sessionStorage.getItem('adoraxe_admin_key');
    if (adminKey) {
      headers['X-Admin-Key'] = adminKey;
    }
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    if (typeof window !== 'undefined') {
      sessionStorage.removeItem('adoraxe_admin_key');
      window.dispatchEvent(new CustomEvent('adoraxe:unauthorized'));
    }
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Network error' }));
    const detail = error.detail;
    let msg = detail;
    let code: string | undefined = undefined;
    let action: string | undefined = undefined;

    if (typeof detail === 'object' && detail !== null) {
      msg = detail.message || JSON.stringify(detail);
      code = detail.code;
      action = detail.action;
    }

    const err = new Error(msg || `HTTP ${response.status}`);
    (err as any).code = code;
    (err as any).action = action;
    (err as any).status = response.status;
    throw err;
  }

  return response.json();
}

// --- Auth Verification ---
export const verifyAdminAuth = async (key: string): Promise<boolean> => {
  try {
    const response = await fetch(`${API_URL}/auth/verify`, {
      headers: { 'X-Admin-Key': key.trim() },
    });
    return response.ok;
  } catch {
    return false;
  }
};

// --- Health ---
export const getHealth = () => request<any>('/health');

// --- Channels ---
export const importChannel = (url: string) =>
  request<any>('/channels/import', {
    method: 'POST',
    body: JSON.stringify({ url }),
  });

export const getChannels = () => request<any[]>('/channels');

export const getChannelVideos = (channelId: string, page = 1, pageSize = 24) =>
  request<any>(`/channels/${channelId}/videos?page=${page}&page_size=${pageSize}`);

// --- Videos ---
export const getVideos = (params: { page?: number; page_size?: number; status?: string; search?: string } = {}) => {
  const q = new URLSearchParams();
  if (params.page)      q.set('page',      String(params.page));
  if (params.page_size) q.set('page_size', String(params.page_size));
  if (params.status)    q.set('status',    params.status);
  if (params.search)    q.set('search',    params.search);
  return request<any>(`/videos?${q.toString()}`);
};

export const getVideo = (id: string) => request<any>(`/videos/${id}`);

export const getVideoStreamUrl = (videoId: string, variant: 'original' | 'processed' = 'original'): string =>
  `${API_URL}/videos/${videoId}/stream/${variant}`;

export const uploadVideo = async (file: File, videoId?: string) => {
  const formData = new FormData();
  formData.append('file', file);
  if (videoId) formData.append('video_id', videoId);
  const headers: Record<string, string> = {};
  if (typeof window !== 'undefined') {
    const adminKey = sessionStorage.getItem('adoraxe_admin_key');
    if (adminKey) headers['X-Admin-Key'] = adminKey;
  }
  const response = await fetch(`${API_URL}/videos/upload`, { method: 'POST', body: formData, headers });
  if (response.status === 401) {
    if (typeof window !== 'undefined') {
      sessionStorage.removeItem('adoraxe_admin_key');
      window.dispatchEvent(new CustomEvent('adoraxe:unauthorized'));
    }
  }
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: `Upload failed: HTTP ${response.status}` }));
    throw new Error(err.detail || `Upload failed with status ${response.status}`);
  }
  return response.json();
};

export interface RetrieveMediaOptions {
  mediaUrl?: string;
  autoProcess?: boolean;
  cropMode?: 'blur' | 'pad' | 'crop';
  useYtDlp?: boolean;
  rightsConfirmed?: boolean;
  reuseExisting?: boolean;
}

export const retrieveMedia = (
  videoId: string,
  optionsOrUrl?: string | RetrieveMediaOptions,
  autoProcess = false,
  cropMode: 'blur' | 'pad' | 'crop' = 'blur'
) => {
  let body: any = {};
  if (typeof optionsOrUrl === 'string' || optionsOrUrl === undefined) {
    body = {
      media_url: optionsOrUrl || null,
      auto_process: autoProcess,
      crop_mode: cropMode,
      use_ytdlp: false,
      rights_confirmed: false,
      reuse_existing: true,
    };
  } else {
    body = {
      media_url: optionsOrUrl.mediaUrl || null,
      auto_process: optionsOrUrl.autoProcess ?? autoProcess,
      crop_mode: optionsOrUrl.cropMode ?? cropMode,
      use_ytdlp: optionsOrUrl.useYtDlp ?? false,
      rights_confirmed: optionsOrUrl.rightsConfirmed ?? false,
      reuse_existing: optionsOrUrl.reuseExisting ?? true,
    };
  }
  return request<any>(`/videos/${videoId}/retrieve-media`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
};

export const getYtDlpStatus = () => request<any>('/videos/ytdlp/status');

export const processVideo = (videoId: string, cropMode: 'blur' | 'pad' | 'crop' = 'blur') =>
  request<any>(`/videos/${videoId}/process`, {
    method: 'POST',
    body: JSON.stringify({ crop_mode: cropMode, target_width: 1080, target_height: 1920 }),
  });

export const getJobStatus = (jobId: string) => request<any>(`/videos/jobs/${jobId}`);

// --- Instagram ---
export const getInstagramStatus = (forceRefresh = false) =>
  request<any>(`/instagram/status${forceRefresh ? '?force_refresh=true' : ''}`);
export const refreshInstagramStatus = () => request<any>('/instagram/refresh', { method: 'POST' });
export const getInstagramAccounts = () => request<any[]>('/instagram/accounts');
export const activateInstagramAccount = (accountId: string) =>
  request<any>(`/instagram/accounts/${accountId}/activate`, { method: 'POST' });
export const connectInstagram = () => request<any>('/instagram/connect');
export const disconnectInstagram = () => request<any>('/instagram/disconnect', { method: 'POST' });

// --- Publishing ---
export const getQueueVideos = (accountId?: string) =>
  request<any[]>(`/publishing/queue-videos${accountId ? `?account_id=${accountId}` : ''}`);
export const removeFromQueue = (videoId: string) =>
  request<any>(`/publishing/queue/${videoId}/remove`, { method: 'POST' });
export const addToQueue = (videoId: string) =>
  request<any>(`/publishing/queue/${videoId}/add`, { method: 'POST' });

export const publishReel = (videoId: string, caption: string, rightsConfirmed: boolean, instagramAccountId?: string) =>
  request<any>('/publishing', {
    method: 'POST',
    body: JSON.stringify({
      video_id: videoId,
      caption,
      rights_confirmed: rightsConfirmed,
      instagram_account_id: instagramAccountId || null,
    }),
  });

export const getPublishJob = (jobId: string) => request<any>(`/publishing/${jobId}`);
export const getActivePublishJob = (accountId?: string) =>
  request<{ has_active_job: boolean; job: any | null }>(`/publishing/active${accountId ? `?account_id=${accountId}` : ''}`);
export const reconcilePublishJob = (jobId: string) =>
  request<any>(`/publishing/jobs/${jobId}/reconcile`, { method: 'POST' });

// --- History ---
export const getHistory = (page = 1, pageSize = 20, status?: string) => {
  const q = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (status) q.set('status', status);
  return request<any>(`/history?${q.toString()}`);
};
