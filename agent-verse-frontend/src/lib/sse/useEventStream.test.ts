import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useEventStream } from './useEventStream';
import { useAuthStore } from '@/stores/auth';

function makeReaderFromFrames(frames: string[]) {
  const encoded = frames.map((f) => new TextEncoder().encode(f));
  let n = 0;
  return {
    read: vi.fn().mockImplementation(async () =>
      n < encoded.length ? { done: false, value: encoded[n++] } : { done: true, value: undefined }),
  };
}

describe('useEventStream', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); localStorage.clear(); });
  afterEach(() => {
    vi.useRealTimers();
    useAuthStore.getState().logout();
  });

  it('starts disconnected and does not fetch when path is null', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const { result } = renderHook(() => useEventStream(null));
    expect(result.current.connected).toBe(false);
    expect(result.current.events).toHaveLength(0);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('sends X-API-Key header and the given path', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false, status: 401, body: null,
    } as Response);
    sessionStorage.setItem('av_api_key', 'key-xyz');
    renderHook(() => useEventStream('/governance/approvals/stream'));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/governance/approvals/stream');
    expect(url).not.toContain('api_key=');
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('key-xyz');
  });

  it('parses SSE events and invokes onEvent', async () => {
    const frame = `data: ${JSON.stringify({ type: 'approval_pending', request_id: 'r1' })}\n\n`;
    const encoded = new TextEncoder().encode(frame);
    let n = 0;
    const reader = {
      read: vi.fn().mockImplementation(async () =>
        n++ === 0 ? { done: false, value: encoded } : { done: true, value: undefined }),
    };
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true, status: 200, body: { getReader: () => reader } as unknown as ReadableStream,
    } as Response);
    const onEvent = vi.fn();
    const { result } = renderHook(() => useEventStream('/x', { onEvent }));
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0].type).toBe('approval_pending');
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'approval_pending' }));
  });

  it('does not fetch when enabled is false', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    renderHook(() => useEventStream('/x', { enabled: false }));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('stops on a terminalTypes event and marks the stream disconnected', async () => {
    const frame = `data: ${JSON.stringify({ type: 'goal_completed', event_id: 'e1' })}\n\n`;
    const reader = makeReaderFromFrames([frame]);
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true, status: 200, body: { getReader: () => reader } as unknown as ReadableStream,
    } as Response);
    const { result } = renderHook(() =>
      useEventStream('/x', { terminalTypes: ['goal_completed'] }));
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    await waitFor(() => expect(result.current.connected).toBe(false));
    // Terminal event should suppress any reconnect attempt.
    await new Promise((r) => setTimeout(r, 10));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('ignores malformed JSON frames but keeps processing subsequent valid ones', async () => {
    const badFrame = `data: {not valid json\n\n`;
    const goodFrame = `data: ${JSON.stringify({ type: 'approval_pending', event_id: 'e2' })}\n\n`;
    const reader = makeReaderFromFrames([badFrame + goodFrame]);
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true, status: 200, body: { getReader: () => reader } as unknown as ReadableStream,
    } as Response);
    const { result } = renderHook(() => useEventStream('/x'));
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.events[0].type).toBe('approval_pending');
  });

  it('dedupes events sharing the same event_id', async () => {
    const dup = `data: ${JSON.stringify({ type: 'approval_pending', event_id: 'dup-1' })}\n\n`;
    const reader = makeReaderFromFrames([dup + dup]);
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true, status: 200, body: { getReader: () => reader } as unknown as ReadableStream,
    } as Response);
    const { result } = renderHook(() => useEventStream('/x'));
    await waitFor(() => expect(result.current.events.length).toBeGreaterThan(0));
    // Give the second identical frame a chance to be processed; it must be skipped.
    await new Promise((r) => setTimeout(r, 10));
    expect(result.current.events).toHaveLength(1);
  });

  it('uses the SSO Bearer token header when ssoMode is active', async () => {
    useAuthStore.setState({ ssoMode: true, accessToken: 'jwt-abc' });
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false, status: 500, body: null,
    } as Response);
    renderHook(() => useEventStream('/x'));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer jwt-abc');
    expect(headers['X-API-Key']).toBeUndefined();
  });

  it('falls back to a localStorage api key when sessionStorage has none', async () => {
    localStorage.setItem('av_api_key', 'ls-key');
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false, status: 500, body: null,
    } as Response);
    renderHook(() => useEventStream('/x'));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('ls-key');
  });

  it('schedules a reconnect (without logout) on a non-401/403 error status', async () => {
    vi.useFakeTimers();
    const logoutSpy = vi.spyOn(useAuthStore.getState(), 'logout');
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false, status: 500, body: null,
    } as Response);
    renderHook(() => useEventStream('/x'));
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    expect(logoutSpy).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    expect(logoutSpy).not.toHaveBeenCalled();
  });

  it('calls logout immediately on a 401 without scheduling a reconnect', async () => {
    vi.useFakeTimers();
    const logoutSpy = vi.spyOn(useAuthStore.getState(), 'logout').mockImplementation(() => {});
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false, status: 401, body: null,
    } as Response);
    renderHook(() => useEventStream('/x'));
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    expect(logoutSpy).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(60000);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('does not schedule a reconnect when fetch rejects with an AbortError (unmount)', async () => {
    vi.useFakeTimers();
    let rejectFetch!: (err: unknown) => void;
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementationOnce(
      () => new Promise((_, reject) => { rejectFetch = reject; }));
    const { unmount } = renderHook(() => useEventStream('/x'));
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    unmount();
    const abortError = new Error('The operation was aborted');
    abortError.name = 'AbortError';
    rejectFetch(abortError);
    await vi.advanceTimersByTimeAsync(60000);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('gives up after MAX_RETRIES attempts and leaves the stream disconnected', async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network down'));
    const { result } = renderHook(() => useEventStream('/x'));
    await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    // 8 retries total: delays 1s,2s,4s,8s,16s,30s(cap),30s,30s.
    for (let i = 0; i < 8; i++) {
      await vi.advanceTimersByTimeAsync(30000);
    }
    expect(fetchSpy).toHaveBeenCalledTimes(9);
    expect(result.current.connected).toBe(false);
    // No further attempts scheduled beyond MAX_RETRIES.
    await vi.advanceTimersByTimeAsync(60000);
    expect(fetchSpy).toHaveBeenCalledTimes(9);
  });
});
