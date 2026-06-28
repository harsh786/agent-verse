import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useEventStream } from './useEventStream';

describe('useEventStream', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); localStorage.clear(); });

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
});
