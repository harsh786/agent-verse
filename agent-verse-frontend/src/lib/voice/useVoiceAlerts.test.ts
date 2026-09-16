/**
 * Tests for useVoiceAlerts — subscribes to the proactive-alert SSE stream and
 * plays incoming base64 PCM16 chunks via Web Audio.
 *
 * jsdom has neither EventSource nor AudioContext, so we install tiny fakes on
 * globalThis: a FakeEventSource that records its URL and lets tests fire
 * onmessage/onerror, and a FakeAudioContext with the decode/playback surface
 * the hook calls. fetch is spied for the stream-token exchange. These are
 * transport doubles, NOT source changes.
 */
import { renderHook, act, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { API_BASE } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { useVoiceAlerts, type VoiceAlertEvent } from './useVoiceAlerts';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() { this.closed = true; }

  emit(payload: unknown) {
    this.onmessage?.({ data: typeof payload === 'string' ? payload : JSON.stringify(payload) } as MessageEvent<string>);
  }
  static latest() { return FakeEventSource.instances[FakeEventSource.instances.length - 1]; }
}

class FakeAudioContext {
  static count = 0;
  closed = false;
  destination = {};
  constructor(_opts?: unknown) { FakeAudioContext.count++; }
  createBuffer(_ch: number, len: number) {
    return { getChannelData: () => new Float32Array(len) };
  }
  createBufferSource() {
    return { buffer: null as unknown, connect: vi.fn(), start: vi.fn(), onended: null as (() => void) | null };
  }
  close() { this.closed = true; return Promise.resolve(); }
}

const OriginalES = globalThis.EventSource;
const OriginalAC = (globalThis as unknown as { AudioContext: unknown }).AudioContext;

function b64(bytes: number[]) {
  return btoa(String.fromCharCode(...bytes));
}

beforeEach(() => {
  FakeEventSource.instances = [];
  FakeAudioContext.count = 0;
  (globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource;
  (globalThis as unknown as { AudioContext: unknown }).AudioContext = FakeAudioContext;
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => {
  (globalThis as unknown as { EventSource: unknown }).EventSource = OriginalES;
  (globalThis as unknown as { AudioContext: unknown }).AudioContext = OriginalAC;
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('useVoiceAlerts', () => {
  test('exchanges the api key for a stream token and opens the SSE with ?token=', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ token: 'tok-123' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    renderHook(() => useVoiceAlerts({ enabled: true }));

    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    expect(FakeEventSource.latest().url).toBe(`${API_BASE}/v1/voice/alerts/stream?token=tok-123`);
  });

  test('falls back to ?api_key= when the stream-token mint fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('nope', { status: 500, headers: { 'Content-Type': 'text/plain' } }),
    );
    renderHook(() => useVoiceAlerts({ enabled: true }));

    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    expect(FakeEventSource.latest().url).toBe(`${API_BASE}/v1/voice/alerts/stream?api_key=k`);
  });

  test('parses an incoming event, forwards it to onAlert, and plays its PCM chunks', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ token: 't' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const onAlert = vi.fn();
    renderHook(() => useVoiceAlerts({ enabled: true, onAlert }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));

    const event: VoiceAlertEvent = { event_type: 'mission_failed', text: 'A mission failed', chunks: [b64([0, 1, 2, 3])] };
    await act(async () => { FakeEventSource.latest().emit(event); await Promise.resolve(); });

    expect(onAlert).toHaveBeenCalledWith(event);
    // A single AudioContext is spun up to decode + play the chunk.
    expect(FakeAudioContext.count).toBe(1);
  });

  test('ignores malformed SSE payloads without calling onAlert or throwing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ token: 't' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const onAlert = vi.fn();
    renderHook(() => useVoiceAlerts({ enabled: true, onAlert }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));

    await act(async () => { FakeEventSource.latest().emit('{not-json'); await Promise.resolve(); });
    expect(onAlert).not.toHaveBeenCalled();
  });

  test('does not open a stream when disabled', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }));
    renderHook(() => useVoiceAlerts({ enabled: false }));
    await act(async () => { await Promise.resolve(); });
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  test('does not open a stream when there is no api key', async () => {
    useAuthStore.setState({ apiKey: '' });
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }));
    renderHook(() => useVoiceAlerts({ enabled: true }));
    await act(async () => { await Promise.resolve(); });
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  test('closes the stream on unmount', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ token: 't' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const { unmount } = renderHook(() => useVoiceAlerts({ enabled: true }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const es = FakeEventSource.latest();

    unmount();
    expect(es.closed).toBe(true);
  });

  test('dismiss() closes the active stream', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ token: 't' }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    const { result } = renderHook(() => useVoiceAlerts({ enabled: true }));
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    const es = FakeEventSource.latest();

    act(() => result.current.dismiss());
    expect(es.closed).toBe(true);
  });
});
