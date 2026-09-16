/**
 * Tests for OrgRealtimeManager — the org-level SSE stream manager.
 *
 * jsdom has no EventSource, so we install a tiny test-local fake transport that
 * records the connect URL, lets tests emit open/message/error, and records
 * close(). The manager also mints a short-lived stream token via fetch before
 * opening the stream, so fetch is stubbed too. This is a transport double, NOT
 * a source change.
 */
import type { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { OrgRealtimeManager, ORG_EVENTS, type OrgEvent } from './OrgRealtimeManager';
import { API_BASE } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emitOpen() {
    this.onopen?.();
  }
  emitMessage(data: unknown) {
    this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) });
  }
  emitError() {
    this.onerror?.(new Event('error'));
  }
  static latest(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }
}

const OriginalEventSource = globalThis.EventSource;

function stubTokenFetch(ok = true) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    ok
      ? new Response(JSON.stringify({ token: 'stream-tok' }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      : new Response(JSON.stringify({ detail: 'no' }), { status: 500, headers: { 'Content-Type': 'application/json' } }),
  );
}

function fakeQueryClient() {
  return { invalidateQueries: vi.fn() } as unknown as QueryClient;
}

function baseEvent(overrides: Partial<OrgEvent> = {}): OrgEvent {
  return {
    event_type: ORG_EVENTS.MISSION_CREATED,
    org_id: 'org-1',
    tenant_id: 't-1',
    payload: {},
    timestamp: '2026-01-01T00:00:00Z',
    version: '1',
    ...overrides,
  };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  (globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource;
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => {
  (globalThis as unknown as { EventSource: unknown }).EventSource = OriginalEventSource;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('OrgRealtimeManager', () => {
  test('mints a stream token then opens the org events stream with the token (never the api key)', async () => {
    stubTokenFetch(true);
    const mgr = new OrgRealtimeManager('org-1', 'secret-api-key');
    mgr.connect({});
    await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const es = FakeEventSource.latest();
    expect(es.url).toBe(`${API_BASE}/v1/org/org-1/events/stream?token=stream-tok`);
    expect(es.url).not.toContain('secret-api-key');
    mgr.disconnect();
  });

  test('falls back to the api key in the URL when the token mint fails', async () => {
    stubTokenFetch(false);
    const mgr = new OrgRealtimeManager('org-1', 'secret-api-key');
    mgr.connect({});
    await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.latest().url).toBe(`${API_BASE}/v1/org/org-1/events/stream?api_key=secret-api-key`);
    mgr.disconnect();
  });

  test('invokes onConnected when the stream opens', async () => {
    stubTokenFetch(true);
    const onConnected = vi.fn();
    const mgr = new OrgRealtimeManager('org-1', 'k');
    mgr.connect({ onConnected });
    await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    FakeEventSource.latest().emitOpen();
    expect(onConnected).toHaveBeenCalledTimes(1);
    mgr.disconnect();
  });

  test('parses an incoming event, dispatches cache invalidations, and forwards it to onEvent', async () => {
    stubTokenFetch(true);
    const onEvent = vi.fn();
    const qc = fakeQueryClient();
    const mgr = new OrgRealtimeManager('org-1', 'k');
    mgr.connect({ queryClient: qc, onEvent });
    await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    const event = baseEvent({ event_type: ORG_EVENTS.MISSION_CREATED, payload: { mission_id: 'm1' } });
    FakeEventSource.latest().emitMessage(event);

    expect(onEvent).toHaveBeenCalledWith(event);
    const invalidate = qc.invalidateQueries as unknown as ReturnType<typeof vi.fn>;
    // Mission events invalidate the org missions list...
    expect(
      invalidate.mock.calls.some(([arg]) => JSON.stringify((arg as { queryKey: unknown[] }).queryKey) === JSON.stringify(['orgs', 'org-1', 'missions'])),
    ).toBe(true);
    // ...and the event feed is always refreshed.
    expect(
      invalidate.mock.calls.some(([arg]) => (arg as { queryKey: unknown[] }).queryKey.includes('events')),
    ).toBe(true);
    mgr.disconnect();
  });

  test('ignores a malformed (non-JSON) message without throwing or calling onEvent', async () => {
    stubTokenFetch(true);
    const onEvent = vi.fn();
    const mgr = new OrgRealtimeManager('org-1', 'k');
    mgr.connect({ onEvent });
    await vi.waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(() => FakeEventSource.latest().emitMessage('not-json{')).not.toThrow();
    expect(onEvent).not.toHaveBeenCalled();
    mgr.disconnect();
  });

  test('on stream error, closes the source, notifies onDisconnected, and reconnects after a backoff', async () => {
    vi.useFakeTimers();
    stubTokenFetch(true);
    const onDisconnected = vi.fn();
    const mgr = new OrgRealtimeManager('org-1', 'k');
    mgr.connect({ onDisconnected });
    await vi.advanceTimersByTimeAsync(0);
    expect(FakeEventSource.instances).toHaveLength(1);
    const first = FakeEventSource.latest();

    first.emitError();
    expect(onDisconnected).toHaveBeenCalledTimes(1);
    expect(first.closed).toBe(true);

    // Backoff (2s * 1.5) → a fresh EventSource is opened to the same URL.
    await vi.advanceTimersByTimeAsync(3100);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.latest().url).toBe(first.url);
    mgr.disconnect();
  });

  test('disconnect closes the active stream and cancels any pending reconnect', async () => {
    vi.useFakeTimers();
    stubTokenFetch(true);
    const mgr = new OrgRealtimeManager('org-1', 'k');
    mgr.connect({});
    await vi.advanceTimersByTimeAsync(0);
    const es = FakeEventSource.latest();
    es.emitError(); // schedule a reconnect

    mgr.disconnect();
    expect(es.closed).toBe(true);

    // No new stream should be opened after an intentional disconnect.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});
