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
