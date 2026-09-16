/**
 * Tests for useCollabSocket — the collaboration WebSocket hook.
 *
 * jsdom has no WebSocket, so we install a tiny test-local fake transport that
 * records the URL/protocols, lets tests emit open/message/close/error, and
 * records send()/close(). This is a transport double, NOT a source change.
 */
import { renderHook, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useCollabSocket } from './useCollabSocket';

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  url: string;
  protocols?: string | string[];
  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];
  closed = false;

  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((e: Event) => void) | null = null;

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
    this.readyState = FakeWebSocket.CLOSED;
  }

  // ── test helpers ──
  simulateOpen() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
  simulateMessage(payload: unknown) {
    this.onmessage?.({ data: typeof payload === 'string' ? payload : JSON.stringify(payload) } as MessageEvent<string>);
  }
  simulateClose() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
  simulateError() {
    this.onerror?.(new Event('error'));
  }

  static latest(): FakeWebSocket {
    return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  }
}

const OriginalWebSocket = globalThis.WebSocket;

beforeEach(() => {
  FakeWebSocket.instances = [];
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
});
afterEach(() => {
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = OriginalWebSocket;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function baseProps(overrides: Partial<Parameters<typeof useCollabSocket>[0]> = {}) {
  return {
    sessionId: 'sess-1',
    apiKey: 'secret-key',
    onMessage: vi.fn(),
    ...overrides,
  };
}

describe('useCollabSocket', () => {
  test('opens a socket at the session URL with an av.v1 subprotocol token', () => {
    renderHook(() => useCollabSocket(baseProps()));
    const ws = FakeWebSocket.latest();
    const wsBase = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000';
    expect(ws.url).toBe(`${wsBase}/collab/sessions/sess-1/ws`);
    // The api key is passed as a base64url-encoded subprotocol, never in the URL.
    const proto = (ws.protocols as string[])[0];
    expect(proto).toMatch(/^av\.v1\./);
    expect(ws.url).not.toContain('secret-key');
  });

  test('invokes onOpen when the socket connects', () => {
    const onOpen = vi.fn();
    renderHook(() => useCollabSocket(baseProps({ onOpen })));
    act(() => FakeWebSocket.latest().simulateOpen());
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  test('parses incoming JSON messages and forwards them to onMessage', () => {
    const onMessage = vi.fn();
    renderHook(() => useCollabSocket(baseProps({ onMessage })));
    act(() => FakeWebSocket.latest().simulateMessage({ type: 'cursor', x: 3 }));
    expect(onMessage).toHaveBeenCalledWith({ type: 'cursor', x: 3 });
  });

  test('ignores heartbeat pong frames', () => {
    const onMessage = vi.fn();
    renderHook(() => useCollabSocket(baseProps({ onMessage })));
    act(() => FakeWebSocket.latest().simulateMessage({ type: 'pong' }));
    expect(onMessage).not.toHaveBeenCalled();
  });

  test('sendMessage serialises and sends only when the socket is open', () => {
    const { result } = renderHook(() => useCollabSocket(baseProps()));
    const ws = FakeWebSocket.latest();

    // Still CONNECTING → send is a no-op.
    act(() => result.current.sendMessage({ hello: 1 }));
    expect(ws.sent).toHaveLength(0);

    act(() => ws.simulateOpen());
    act(() => result.current.sendMessage({ hello: 1 }));
    expect(ws.sent).toEqual([JSON.stringify({ hello: 1 })]);
  });

  test('closes the socket on unmount and does not reconnect', () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useCollabSocket(baseProps()));
    const ws = FakeWebSocket.latest();
    act(() => ws.simulateOpen());

    unmount();
    expect(ws.closed).toBe(true);

    // No new socket should be created after an intentional close.
    act(() => vi.advanceTimersByTime(5_000));
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  test('reconnects with backoff after an unexpected close', () => {
    vi.useFakeTimers();
    renderHook(() => useCollabSocket(baseProps()));
    const first = FakeWebSocket.latest();
    act(() => first.simulateOpen());

    // Server drops the connection unexpectedly.
    act(() => first.simulateClose());
    expect(FakeWebSocket.instances).toHaveLength(1);

    // First backoff is 1s → a fresh socket is created to the same URL.
    act(() => vi.advanceTimersByTime(1_000));
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(FakeWebSocket.latest().url).toBe(first.url);
  });

  test('does not reconnect when reconnect is disabled', () => {
    vi.useFakeTimers();
    renderHook(() => useCollabSocket(baseProps({ reconnect: false })));
    const ws = FakeWebSocket.latest();
    act(() => ws.simulateOpen());
    act(() => ws.simulateClose());
    act(() => vi.advanceTimersByTime(5_000));
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
