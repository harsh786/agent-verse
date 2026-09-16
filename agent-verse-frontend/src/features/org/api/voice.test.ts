/**
 * Tests for voiceApi — the Voice OS API client.
 *
 * Each wrapper is exercised with a fetch spy: we assert the URL, method,
 * headers, and (where applicable) the request body, then check the parsed /
 * blob return value. Auth is seeded into useAuthStore so getAuthHeader() /
 * getApiKey() emit the X-API-Key header the real app sends.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { voiceApi } from './voice';
import { API_BASE } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';

const BASE = `${API_BASE}/v1/voice`;

interface Captured {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
  rawBody: unknown;
}

function mockFetch(body: BodyInit | null, status = 200, headers: Record<string, string> = { 'Content-Type': 'application/json' }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(body, { status, headers }));
}

function lastCall(spy: ReturnType<typeof mockFetch>): Captured {
  const call = spy.mock.calls[spy.mock.calls.length - 1];
  const [url, init] = call as [string, RequestInit | undefined];
  const rawBody = init?.body;
  return {
    url: String(url),
    method: (init?.method ?? 'GET').toUpperCase(),
    headers: (init?.headers ?? {}) as Record<string, string>,
    body: rawBody && typeof rawBody === 'string' ? JSON.parse(rawBody) : undefined,
    rawBody,
  };
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({
    apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '',
  });
});
afterEach(() => vi.restoreAllMocks());

describe('voiceApi', () => {
  test('status() GETs /v1/voice/status with the tenant key and returns the parsed body', async () => {
    const payload = { stt_status: 'ready', tts_status: 'ready', stt_model: 'w', tts_model: 'k', device: 'cpu', stt_provider: 'faster-whisper', tts_provider: 'kokoro' };
    const spy = mockFetch(JSON.stringify(payload));

    const result = await voiceApi.status();

    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/status`);
    expect(c.method).toBe('GET');
    expect(c.headers['X-API-Key']).toBe('k');
    expect(result).toEqual(payload);
  });

  test('transcribe() POSTs multipart FormData to /transcribe and returns the parsed transcript', async () => {
    const payload = { transcript: 'hello world', language: 'en', confidence: 0.9, segments: [], duration_s: 1.2 };
    const spy = mockFetch(JSON.stringify(payload));

    const result = await voiceApi.transcribe(new Blob(['abc'], { type: 'audio/wav' }), 'clip.wav');

    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/transcribe`);
    expect(c.method).toBe('POST');
    expect(c.rawBody).toBeInstanceOf(FormData);
    expect((c.rawBody as FormData).get('audio')).toBeInstanceOf(File);
    // FormData upload must NOT force a JSON content-type (would break the boundary).
    expect(c.headers['Content-Type']).toBeUndefined();
    expect(result).toEqual(payload);
  });

  test('speak() POSTs JSON {text, language} to /speak and returns a Blob', async () => {
    const spy = mockFetch('WAVBYTES', 200, { 'Content-Type': 'audio/wav' });

    const result = await voiceApi.speak('good morning');

    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/speak`);
    expect(c.method).toBe('POST');
    expect(c.headers['Content-Type']).toBe('application/json');
    expect(c.headers['X-API-Key']).toBe('k');
    expect(c.body).toEqual({ text: 'good morning', language: 'en' });
    expect(result).toBeInstanceOf(Blob);
  });

  test('speak() merges opts (language override + persona flags) into the body', async () => {
    const spy = mockFetch('X', 200, { 'Content-Type': 'audio/wav' });

    await voiceApi.speak('hola', { language: 'es', org_id: 'org-1', use_org_persona: true, speed: 1.1 });

    expect(lastCall(spy).body).toEqual({
      text: 'hola', language: 'es', org_id: 'org-1', use_org_persona: true, speed: 1.1,
    });
  });

  test('speak() throws the server detail message on a non-OK response', async () => {
    mockFetch(JSON.stringify({ detail: 'engine down' }), 500);
    await expect(voiceApi.speak('x')).rejects.toThrow('engine down');
  });

  test('greeting() GETs /greeting/:orgId with user_name + language query params and returns a Blob', async () => {
    const spy = mockFetch('GREETWAV', 200, { 'Content-Type': 'audio/wav' });

    const result = await voiceApi.greeting('org-9', { user_name: 'Ada', language: 'en' });

    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/greeting/org-9?user_name=Ada&language=en`);
    expect(c.method).toBe('GET');
    expect(c.headers['X-API-Key']).toBe('k');
    expect(result).toBeInstanceOf(Blob);
  });

  test('greeting() throws when the fetch is not OK', async () => {
    mockFetch('nope', 404, { 'Content-Type': 'text/plain' });
    await expect(voiceApi.greeting('org-9')).rejects.toThrow('Greeting fetch failed');
  });

  test('uploadPersona() POSTs FormData to /persona/:orgId with ref_text + language in the query', async () => {
    const payload = { org_id: 'org-2', tenant_id: 't', ref_audio_url: 'u', ref_text: 'sample text', language: 'en', created_at: 'x' };
    const spy = mockFetch(JSON.stringify(payload));

    const result = await voiceApi.uploadPersona('org-2', new File(['a'], 'ref.wav'), 'sample text', 'en');

    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/persona/org-2?ref_text=sample%20text&language=en`);
    expect(c.method).toBe('POST');
    expect(c.rawBody).toBeInstanceOf(FormData);
    expect((c.rawBody as FormData).get('audio')).toBeInstanceOf(File);
    expect(result).toEqual(payload);
  });

  test('deletePersona() issues a DELETE to /persona/:orgId (204 → undefined)', async () => {
    const spy = mockFetch(null, 204, {});

    const result = await voiceApi.deletePersona('org-3');

    const c = lastCall(spy);
    expect(c.url).toBe(`${BASE}/persona/org-3`);
    expect(c.method).toBe('DELETE');
    expect(result).toBeUndefined();
  });

  test('streamUrl() builds a ws:// URL carrying the (encoded) api key', () => {
    useAuthStore.setState({ apiKey: 'key/with+chars' });
    const url = voiceApi.streamUrl('org-4');
    const wsBase = API_BASE.replace(/^http/, 'ws');
    expect(url).toBe(`${wsBase}/v1/voice/stream/org-4?api_key=${encodeURIComponent('key/with+chars')}`);
    expect(url.startsWith('ws')).toBe(true);
  });
});
