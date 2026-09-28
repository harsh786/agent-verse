/**
 * Unit tests for useChatStream hook.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStream } from './hooks/useChatStream';

// Mock chatApi: the hook mints a short-lived stream token before every
// (re)connect and passes it to streamUrl.
const mocks = vi.hoisted(() => ({
  streamToken: vi.fn<() => Promise<string>>(),
}));
vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    streamToken: mocks.streamToken,
    streamUrl: (sessionId: string, messageId: string, token: string) =>
      `http://test/chat/sessions/${sessionId}/stream?message_id=${messageId}&token=${token}`,
  },
}));

// open() is async (awaits the token mint) — flush pending microtasks so the
// EventSource exists before a test drives it.
const flush = () =>
  act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });

// Mock EventSource globally
class MockEventSource {
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
  }

  close() {
    this.closed = true;
  }

  // Test helper: simulate a message
  emit(data: object) {
    if (this.onmessage) {
      this.onmessage(new MessageEvent('message', { data: JSON.stringify(data) }));
    }
  }
}

let mockEs: MockEventSource | null = null;
vi.stubGlobal('EventSource', class {
  constructor(url: string) {
    mockEs = new MockEventSource(url);
    return mockEs;
  }
} as unknown as typeof EventSource);

describe('useChatStream', () => {
  beforeEach(() => {
    mockEs = null;
    mocks.streamToken.mockReset();
    mocks.streamToken.mockResolvedValue('stream-tok');
  });

  it('starts in non-streaming state', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    expect(result.current.isStreaming).toBe(false);
  });

  it('sets isStreaming to true when stream starts', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    expect(result.current.isStreaming).toBe(true);
  });

  it('accumulates token events', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    await flush();
    act(() => {
      mockEs?.emit({ type: 'token', token: 'Hello' });
      mockEs?.emit({ type: 'token', token: ' World' });
    });
    expect(result.current.tokens).toBe('Hello World');
  });

  it('sets isStreaming to false on done event', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    await flush();
    act(() => {
      mockEs?.emit({ type: 'done', session_id: 's1', message_id: 'm1' });
    });
    expect(result.current.isStreaming).toBe(false);
  });

  it('calls onDone callback with accumulated tokens', async () => {
    const onDone = vi.fn();
    const { result } = renderHook(() => useChatStream('s1', onDone));
    act(() => {
      result.current.startStream('m1');
    });
    await flush();
    act(() => {
      mockEs?.emit({ type: 'token', token: 'hi' });
      mockEs?.emit({ type: 'done', session_id: 's1', message_id: 'm1' });
    });
    expect(onDone).toHaveBeenCalledWith('hi');
  });

  it('tracks reasoning tokens separately', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    await flush();
    act(() => {
      mockEs?.emit({ type: 'reasoning', token: 'thinking...' });
    });
    expect(result.current.reasoning).toBe('thinking...');
  });

  it('reconnects (not a hard error) on the first onerror', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    await flush();
    expect(mockEs).not.toBeNull();
    act(() => {
      mockEs?.onerror?.();
    });
    // First drop schedules a reconnect: still streaming, no hard error yet.
    expect(result.current.error).toBeNull();
    expect(result.current.isStreaming).toBe(true);
    expect(result.current.reconnecting).toBe(true);
    vi.useRealTimers();
  });

  it('opens the stream with a freshly minted token and never the api key', async () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    // Nothing is opened until the token mint resolves.
    expect(mockEs).toBeNull();
    await flush();
    expect(mocks.streamToken).toHaveBeenCalledTimes(1);
    expect(mockEs?.url).toBe('http://test/chat/sessions/s1/stream?message_id=m1&token=stream-tok');
    expect(mockEs?.url).not.toContain('api_key=');
  });

  it('a failed token mint opens no EventSource and surfaces an error', async () => {
    mocks.streamToken.mockRejectedValue(new Error('HTTP 401'));
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    await flush();
    expect(mockEs).toBeNull();
    expect(result.current.error).toBe('Could not authorize the stream');
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.reconnecting).toBe(false);
  });

  it('stopping before the token mint resolves opens no EventSource', async () => {
    let resolveToken!: (t: string) => void;
    mocks.streamToken.mockImplementation(() => new Promise((r) => { resolveToken = r; }));
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    act(() => {
      result.current.stopStream();
    });
    resolveToken('late-tok');
    await flush();
    expect(mockEs).toBeNull();
    expect(result.current.isStreaming).toBe(false);
  });

  it('stopStream halts streaming', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    act(() => {
      result.current.stopStream();
    });
    expect(result.current.isStreaming).toBe(false);
  });
});
