/**
 * Unit tests for useChatStream hook.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStream } from './hooks/useChatStream';

// Mock chatApi.streamUrl
vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    streamUrl: (sessionId: string, messageId: string) =>
      `http://test/chat/sessions/${sessionId}/stream?message_id=${messageId}`,
  },
}));

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

  it('accumulates token events', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    act(() => {
      mockEs?.emit({ type: 'token', token: 'Hello' });
      mockEs?.emit({ type: 'token', token: ' World' });
    });
    expect(result.current.tokens).toBe('Hello World');
  });

  it('sets isStreaming to false on done event', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    act(() => {
      mockEs?.emit({ type: 'done', session_id: 's1', message_id: 'm1' });
    });
    expect(result.current.isStreaming).toBe(false);
  });

  it('calls onDone callback with accumulated tokens', () => {
    const onDone = vi.fn();
    const { result } = renderHook(() => useChatStream('s1', onDone));
    act(() => {
      result.current.startStream('m1');
    });
    act(() => {
      mockEs?.emit({ type: 'token', token: 'hi' });
      mockEs?.emit({ type: 'done', session_id: 's1', message_id: 'm1' });
    });
    expect(onDone).toHaveBeenCalledWith('hi');
  });

  it('tracks reasoning tokens separately', () => {
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    act(() => {
      mockEs?.emit({ type: 'reasoning', token: 'thinking...' });
    });
    expect(result.current.reasoning).toBe('thinking...');
  });

  it('reconnects (not a hard error) on the first onerror', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useChatStream('s1'));
    act(() => {
      result.current.startStream('m1');
    });
    act(() => {
      mockEs?.onerror?.();
    });
    // First drop schedules a reconnect: still streaming, no hard error yet.
    expect(result.current.error).toBeNull();
    expect(result.current.isStreaming).toBe(true);
    expect(result.current.reconnecting).toBe(true);
    vi.useRealTimers();
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
