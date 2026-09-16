/**
 * Chat API client — wraps all /chat endpoints.
 */

import { API_BASE } from './client';
import { useAuthStore } from '@/stores/auth';
import type {
  ChatSession,
  ChatMessage,
  ChatFolder,
  ChatArtifact,
  ChatUsageSummary,
  DispatchResult,
  CreateSessionPayload,
  UpdateSessionPayload,
} from '@/features/chat/types/chat.types';

function getApiKey(): string {
  // Canonical source is the auth store; fall back to the same session/local keys
  // the shared client uses. (Previously read a stale 'agentverse_api_key' that
  // nothing writes → empty X-API-Key → every chat call 401'd and "New Chat"
  // silently did nothing.)
  return (
    useAuthStore.getState().apiKey ||
    sessionStorage.getItem('av_api_key') ||
    localStorage.getItem('av_api_key') ||
    ''
  );
}

function headers(): HeadersInit {
  // SSO parity: send a Bearer token under Keycloak SSO, else the API key.
  const { ssoMode, accessToken } = useAuthStore.getState();
  if (ssoMode && accessToken) {
    return { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` };
  }
  return {
    'Content-Type': 'application/json',
    'X-API-Key': getApiKey(),
  };
}

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Sessions ─────────────────────────────────────────────────────────────────

export const chatApi = {
  // Sessions
  createSession: (payload: CreateSessionPayload = {}): Promise<ChatSession> =>
    fetch(`${API_BASE}/chat/sessions`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify(payload),
    }).then((r) => _json<ChatSession>(r)),

  // TODO(scale): GET /chat/sessions returns every session for the tenant with no
  // server-side limit/offset/search params (verified against the chat router).
  // We cap the rendered set client-side to `limit` (most-recent first, the order
  // the backend already returns) so the sidebar DOM stays bounded, and the
  // ChatSidebar search runs over this loaded page only. Needs a backend
  // limit/offset/cursor + search param to page/search the full history.
  listSessions: (limit = 100): Promise<{ sessions: ChatSession[] }> =>
    fetch(`${API_BASE}/chat/sessions`, { headers: headers() })
      .then((r) => _json<{ sessions: ChatSession[] }>(r))
      .then((data) => ({ sessions: (data.sessions ?? []).slice(0, limit) })),

  getSession: (sessionId: string): Promise<ChatSession> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}`, { headers: headers() }).then((r) =>
      _json<ChatSession>(r),
    ),

  updateSession: (sessionId: string, payload: UpdateSessionPayload): Promise<ChatSession> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}`, {
      method: 'PATCH',
      headers: headers(),
      body: JSON.stringify(payload),
    }).then((r) => _json<ChatSession>(r)),

  deleteSession: (sessionId: string): Promise<void> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}`, {
      method: 'DELETE',
      headers: headers(),
    }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}`);
    }),

  pinSession: (sessionId: string, pinned: boolean): Promise<ChatSession> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/pin?pinned=${pinned}`, {
      method: 'POST',
      headers: headers(),
    }).then((r) => _json<ChatSession>(r)),

  moveToFolder: (sessionId: string, folderId: string | null): Promise<ChatSession> =>
    fetch(
      `${API_BASE}/chat/sessions/${sessionId}/move${folderId ? `?folder_id=${folderId}` : ''}`,
      { method: 'POST', headers: headers() },
    ).then((r) => _json<ChatSession>(r)),

  summarizeSession: (sessionId: string): Promise<{ summary: string }> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/summarize`, {
      method: 'POST',
      headers: headers(),
    }).then((r) => _json<{ summary: string }>(r)),

  getUsage: (sessionId: string): Promise<ChatUsageSummary> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/usage`, { headers: headers() }).then((r) =>
      _json<ChatUsageSummary>(r),
    ),

  // Messages
  listMessages: (
    sessionId: string,
    limit = 100,
  ): Promise<{ messages: ChatMessage[] }> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/messages?limit=${limit}`, {
      headers: headers(),
    }).then((r) => _json<{ messages: ChatMessage[] }>(r)),

  sendMessage: (sessionId: string, content: string, modelOverride?: string): Promise<DispatchResult> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/messages`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ content, model_override: modelOverride }),
    }).then((r) => _json<DispatchResult>(r)),

  editMessage: (
    sessionId: string,
    messageId: string,
    content: string,
  ): Promise<{ message: ChatMessage; pruned_message_ids: string[] }> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/messages/${messageId}`, {
      method: 'PATCH',
      headers: headers(),
      body: JSON.stringify({ content }),
    }).then((r) => _json<{ message: ChatMessage; pruned_message_ids: string[] }>(r)),

  deleteMessage: (sessionId: string, messageId: string): Promise<void> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/messages/${messageId}`, {
      method: 'DELETE',
      headers: headers(),
    }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}`);
    }),

  // SSE stream URL helper
  streamUrl: (sessionId: string, messageId: string): string =>
    `${API_BASE}/chat/sessions/${sessionId}/stream?message_id=${messageId}&api_key=${encodeURIComponent(getApiKey())}`,

  // Download URL for a chat-generated artifact/document. The api_key is passed
  // as a query param (the tenant middleware accepts it) because a plain browser
  // navigation cannot set the X-API-Key header.
  artifactDownloadUrl: (artifactId: string): string =>
    `${API_BASE}/chat/artifacts/${artifactId}/download?api_key=${encodeURIComponent(getApiKey())}`,

  // Search
  search: (
    query: string,
    sessionId?: string,
    limit = 20,
  ): Promise<{ results: ChatMessage[]; total: number }> =>
    fetch(`${API_BASE}/chat/search`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ query, session_id: sessionId, limit }),
    }).then((r) => _json<{ results: ChatMessage[]; total: number }>(r)),

  // Folders
  listFolders: (): Promise<{ folders: ChatFolder[] }> =>
    fetch(`${API_BASE}/chat/folders`, { headers: headers() }).then((r) =>
      _json<{ folders: ChatFolder[] }>(r),
    ),

  createFolder: (name: string, color = '#6366f1'): Promise<ChatFolder> =>
    fetch(`${API_BASE}/chat/folders`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ name, color }),
    }).then((r) => _json<ChatFolder>(r)),

  deleteFolder: (folderId: string): Promise<void> =>
    fetch(`${API_BASE}/chat/folders/${folderId}`, {
      method: 'DELETE',
      headers: headers(),
    }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}`);
    }),

  // Artifacts
  listArtifacts: (sessionId: string): Promise<{ artifacts: ChatArtifact[] }> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/artifacts`, {
      headers: headers(),
    }).then((r) => _json<{ artifacts: ChatArtifact[] }>(r)),

  createArtifact: (
    sessionId: string,
    title: string,
    language: string,
    content: string,
    messageId?: string,
  ): Promise<ChatArtifact> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/artifacts`, {
      method: 'POST',
      headers: headers(),
      body: JSON.stringify({ title, language, content, message_id: messageId }),
    }).then((r) => _json<ChatArtifact>(r)),

  updateArtifact: (
    sessionId: string,
    artifactId: string,
    content: string,
  ): Promise<ChatArtifact> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/artifacts/${artifactId}`, {
      method: 'PATCH',
      headers: headers(),
      body: JSON.stringify({ content }),
    }).then((r) => _json<ChatArtifact>(r)),

  deleteArtifact: (sessionId: string, artifactId: string): Promise<void> =>
    fetch(`${API_BASE}/chat/sessions/${sessionId}/artifacts/${artifactId}`, {
      method: 'DELETE',
      headers: headers(),
    }).then((r) => {
      if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}`);
    }),

  // Attachments — upload a file the agent can OCR/vision-process at run time.
  // Multipart form-data: only the API key header is set so the browser can add
  // the multipart boundary itself (mirrors org/api.ts uploadAttachment).
  uploadAttachment: (
    sessionId: string,
    file: File,
  ): Promise<{
    attachment_id: string;
    filename: string;
    content_type: string;
    size: number;
    url?: string;
  }> => {
    const fd = new FormData();
    fd.append('file', file);
    return fetch(`${API_BASE}/chat/sessions/${sessionId}/attachments`, {
      method: 'POST',
      headers: { 'X-API-Key': getApiKey() },
      body: fd,
    }).then((r) =>
      _json<{
        attachment_id: string;
        filename: string;
        content_type: string;
        size: number;
        url?: string;
      }>(r),
    );
  },

  // Models
  listModels: (): Promise<{ models: string[] }> =>
    fetch(`${API_BASE}/chat/models`, { headers: headers() }).then((r) =>
      _json<{ models: string[] }>(r),
    ),
};
