/**
 * Tests for the chatApi client — asserts each wrapper issues the correct
 * URL + method (+ body + headers) and returns the parsed JSON body. chatApi
 * talks to fetch directly (not apiFetch), building headers from the auth store.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { chatApi } from './chat';
import { API_BASE } from './client';
import { useAuthStore } from '@/stores/auth';

const BASE = API_BASE;

interface Captured {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

function mockFetch(payload: unknown, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
    const body = status === 204 ? null : JSON.stringify(payload);
    return new Response(body, { status, headers: { 'Content-Type': 'application/json' } });
  });
}

function lastCall(spy: ReturnType<typeof mockFetch>): Captured {
  const call = spy.mock.calls[spy.mock.calls.length - 1];
  const [url, init] = call as [string, RequestInit | undefined];
  return {
    url: String(url),
    method: (init?.method ?? 'GET').toUpperCase(),
    headers: (init?.headers ?? {}) as Record<string, string>,
    body: init?.body && typeof init.body === 'string' ? JSON.parse(init.body) : init?.body,
  };
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k-123', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('chatApi sessions', () => {
  test('createSession POSTs /chat/sessions with the tenant key and returns the parsed session', async () => {
    const session = { id: 's1', title: 'New Chat', pinned: false };
    const spy = mockFetch(session);
    const result = await chatApi.createSession({ title: 'New Chat' });
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions`);
    expect(c.method).toBe('POST');
    expect(c.headers['X-API-Key']).toBe('k-123');
    expect(c.headers['Content-Type']).toBe('application/json');
    expect(c.body).toEqual({ title: 'New Chat' });
    expect(result).toEqual(session);
  });

  test('createSession defaults the payload to {} when omitted', async () => {
    const spy = mockFetch({ id: 's2' });
    await chatApi.createSession();
    expect(lastCall(spy).body).toEqual({});
  });

  test('listSessions GETs /chat/sessions and caps the returned set to the limit', async () => {
    const sessions = Array.from({ length: 5 }, (_, i) => ({ id: `s${i}` }));
    const spy = mockFetch({ sessions });
    const result = await chatApi.listSessions(3);
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions`);
    expect(c.method).toBe('GET');
    expect(result.sessions).toHaveLength(3);
  });

  test('listSessions tolerates a missing sessions array', async () => {
    mockFetch({});
    const result = await chatApi.listSessions();
    expect(result.sessions).toEqual([]);
  });

  test('getSession GETs /chat/sessions/:id', async () => {
    const spy = mockFetch({ id: 's9' });
    const result = await chatApi.getSession('s9');
    expect(lastCall(spy).url).toBe(`${BASE}/chat/sessions/s9`);
    expect(result.id).toBe('s9');
  });

  test('updateSession PATCHes with the payload body', async () => {
    const spy = mockFetch({ id: 's1', pinned: true });
    await chatApi.updateSession('s1', { pinned: true, title: 'Renamed' });
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions/s1`);
    expect(c.method).toBe('PATCH');
    expect(c.body).toEqual({ pinned: true, title: 'Renamed' });
  });

  test('deleteSession DELETEs and resolves on a 204', async () => {
    const spy = mockFetch(undefined, 204);
    await expect(chatApi.deleteSession('s1')).resolves.toBeUndefined();
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions/s1`);
    expect(c.method).toBe('DELETE');
  });

  test('pinSession POSTs to the pin endpoint with the pinned query param', async () => {
    const spy = mockFetch({ id: 's1', pinned: true });
    await chatApi.pinSession('s1', true);
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions/s1/pin?pinned=true`);
    expect(c.method).toBe('POST');
  });

  test('moveToFolder appends folder_id when provided and omits it when null', async () => {
    const spy = mockFetch({ id: 's1' });
    await chatApi.moveToFolder('s1', 'f1');
    expect(lastCall(spy).url).toBe(`${BASE}/chat/sessions/s1/move?folder_id=f1`);
    await chatApi.moveToFolder('s1', null);
    expect(lastCall(spy).url).toBe(`${BASE}/chat/sessions/s1/move`);
  });
});

describe('chatApi messages', () => {
  test('listMessages GETs the messages endpoint with the limit query', async () => {
    const spy = mockFetch({ messages: [] });
    await chatApi.listMessages('s1', 25);
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions/s1/messages?limit=25`);
    expect(c.method).toBe('GET');
  });

  test('sendMessage POSTs content + model_override and returns the dispatch result', async () => {
    const dispatch = { intent: 'QA', message_id: 'm1', session_id: 's1', clarify_request: null, schedule_confirmation: null };
    const spy = mockFetch(dispatch);
    const result = await chatApi.sendMessage('s1', 'hello', 'gpt-x');
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions/s1/messages`);
    expect(c.method).toBe('POST');
    expect(c.body).toEqual({ content: 'hello', model_override: 'gpt-x' });
    expect(result).toEqual(dispatch);
  });

  test('editMessage PATCHes the message and returns the pruned ids', async () => {
    const spy = mockFetch({ message: { id: 'm1' }, pruned_message_ids: ['m2'] });
    const result = await chatApi.editMessage('s1', 'm1', 'edited');
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions/s1/messages/m1`);
    expect(c.method).toBe('PATCH');
    expect(c.body).toEqual({ content: 'edited' });
    expect(result.pruned_message_ids).toEqual(['m2']);
  });

  test('search POSTs the query with the optional session filter', async () => {
    const spy = mockFetch({ results: [], total: 0 });
    await chatApi.search('needle', 's1', 10);
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/search`);
    expect(c.method).toBe('POST');
    expect(c.body).toEqual({ query: 'needle', session_id: 's1', limit: 10 });
  });
});

describe('chatApi artifacts, folders, uploads and url helpers', () => {
  test('createArtifact POSTs the artifact fields including message_id', async () => {
    const spy = mockFetch({ id: 'a1', title: 'code.py', language: 'python', content: 'print(1)' });
    await chatApi.createArtifact('s1', 'code.py', 'python', 'print(1)', 'm1');
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions/s1/artifacts`);
    expect(c.method).toBe('POST');
    expect(c.body).toEqual({ title: 'code.py', language: 'python', content: 'print(1)', message_id: 'm1' });
  });

  test('createFolder POSTs name + default color', async () => {
    const spy = mockFetch({ id: 'f1', name: 'Work', color: '#6366f1' });
    await chatApi.createFolder('Work');
    expect(lastCall(spy).body).toEqual({ name: 'Work', color: '#6366f1' });
  });

  test('uploadAttachment sends multipart FormData with only the API key header (no Content-Type)', async () => {
    const spy = mockFetch({ attachment_id: 'att1', filename: 'f.png', content_type: 'image/png', size: 3 });
    const file = new File(['abc'], 'f.png', { type: 'image/png' });
    const result = await chatApi.uploadAttachment('s1', file);
    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/chat/sessions/s1/attachments`);
    expect(c.method).toBe('POST');
    expect(c.body).toBeInstanceOf(FormData);
    expect(c.headers['X-API-Key']).toBe('k-123');
    expect(c.headers['Content-Type']).toBeUndefined();
    expect(result.attachment_id).toBe('att1');
  });

  test('streamUrl and artifactDownloadUrl embed the url-encoded api key without hitting fetch', () => {
    const stream = chatApi.streamUrl('s1', 'm1');
    expect(stream).toBe(`${BASE}/chat/sessions/s1/stream?message_id=m1&api_key=k-123`);
    const download = chatApi.artifactDownloadUrl('a1');
    expect(download).toBe(`${BASE}/chat/artifacts/a1/download?api_key=k-123`);
  });

  test('listModels GETs /chat/models and returns the parsed list', async () => {
    const spy = mockFetch({ models: ['m-a', 'm-b'] });
    const result = await chatApi.listModels();
    expect(lastCall(spy).url).toBe(`${BASE}/chat/models`);
    expect(result.models).toEqual(['m-a', 'm-b']);
  });

  test('a non-ok response is surfaced as an HTTP error', async () => {
    mockFetch({ detail: 'nope' }, 500);
    await expect(chatApi.getSession('s1')).rejects.toThrow(/HTTP 500/);
  });
});

describe('chatApi SSO parity', () => {
  test('sends a Bearer token instead of the API key under SSO mode', async () => {
    useAuthStore.setState({ ssoMode: true, accessToken: 'jwt-abc' });
    const spy = mockFetch({ id: 's1' });
    await chatApi.getSession('s1');
    const c = lastCall(spy);
    expect(c.headers['Authorization']).toBe('Bearer jwt-abc');
    expect(c.headers['X-API-Key']).toBeUndefined();
  });
});
