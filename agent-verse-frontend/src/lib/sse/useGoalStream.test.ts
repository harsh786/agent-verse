import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useGoalStream } from './useGoalStream';

describe('useGoalStream', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('starts disconnected', () => {
    const { result } = renderHook(() => useGoalStream(null));
    expect(result.current.connected).toBe(false);
    expect(result.current.events).toHaveLength(0);
  });

  it('sends X-API-Key header not query param', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 401,
      body: null,
    } as Response);

    localStorage.setItem('av_api_key', 'test-key-abc');
    renderHook(() => useGoalStream('goal-1'));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).not.toContain('api_key=');
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('test-key-abc');
  });

  it('parses SSE events from stream', async () => {
    const eventData = JSON.stringify({ type: 'goal_started', goal: 'test' });
    const sseFrame = `data: ${eventData}\n\n`;
    const encoder = new TextEncoder();
    const encoded = encoder.encode(sseFrame);

    let readCount = 0;
    const mockReader = {
      read: vi.fn().mockImplementation(async () => {
        if (readCount === 0) { readCount++; return { done: false, value: encoded }; }
        return { done: true, value: undefined };
      }),
    };

    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: { getReader: () => mockReader } as unknown as ReadableStream,
    } as Response);

    const { result } = renderHook(() => useGoalStream('goal-2'));
    // waitFor retries until the callback does not throw — use expect() so it
    // throws (and retries) while events is still empty.
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0].type).toBe('goal_started');
  });
});

describe('useGoalStream API key source', () => {
  it('reads from sessionStorage first', () => {
    sessionStorage.setItem('av_api_key', 'session-key-123');
    localStorage.removeItem('av_api_key');

    const key = sessionStorage.getItem('av_api_key') ?? localStorage.getItem('av_api_key') ?? '';
    expect(key).toBe('session-key-123');
    sessionStorage.removeItem('av_api_key');
  });

  it('falls back to localStorage for backward compatibility', () => {
    sessionStorage.removeItem('av_api_key');
    localStorage.setItem('av_api_key', 'local-key-456');

    const key = sessionStorage.getItem('av_api_key') ?? localStorage.getItem('av_api_key') ?? '';
    expect(key).toBe('local-key-456');
    localStorage.removeItem('av_api_key');
  });

  it('hook uses sessionStorage key when both are set', async () => {
    sessionStorage.setItem('av_api_key', 'session-wins');
    localStorage.setItem('av_api_key', 'local-loses');

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 401,
      body: null,
    } as Response);

    renderHook(() => useGoalStream('goal-session'));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('session-wins');

    sessionStorage.removeItem('av_api_key');
    localStorage.removeItem('av_api_key');
  });
});
