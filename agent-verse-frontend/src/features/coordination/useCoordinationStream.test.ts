/**
 * Tests for useCoordinationStream — fetch/ReadableStream SSE hook that parses
 * sequenced coordination events, filters stale sequences, and tracks status.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useCoordinationStream } from './useCoordinationStream';
import { useAuthStore } from '@/stores/auth';
import type { CoordinationEvent } from './types';

function makeSseStream(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i >= frames.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(frames[i++]));
    },
  });
}

const frame = (evt: Partial<CoordinationEvent> & { sequence: number }): string =>
  `data: ${JSON.stringify(evt)}\n\n`;

function streamResponse(frames: string[]): Response {
  return new Response(makeSseStream(frames), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'coord-key', tenantId: 't', plan: 'free', isAuthenticated: true, ssoMode: false, accessToken: '' });
});
afterEach(() => vi.restoreAllMocks());

describe('useCoordinationStream', () => {
  test('stays idle and issues no request for an empty session id', () => {
    const spy = vi.spyOn(globalThis, 'fetch');
    const { result } = renderHook(() => useCoordinationStream(''));
    expect(spy).not.toHaveBeenCalled();
    expect(result.current.status).toBe('idle');
    expect(result.current.events).toEqual([]);
  });

  test('connects, goes live, and accumulates sequenced events', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      streamResponse([
        frame({ event_id: 'a', sequence: 1, event_type: 'message' }),
        frame({ event_id: 'b', sequence: 2, event_type: 'message' }),
      ]),
    );

    const { result } = renderHook(() => useCoordinationStream('sess-1'));

    await waitFor(() => expect(result.current.status).toBe('live'));
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(result.current.events.map((e) => e.sequence)).toEqual([1, 2]);
    expect(result.current.lastSequence).toBe(2);
  });

  test('drops events whose sequence is not greater than the cursor', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      streamResponse([
        frame({ event_id: 'a', sequence: 1, event_type: 'm' }),
        frame({ event_id: 'b', sequence: 2, event_type: 'm' }),
        frame({ event_id: 'stale', sequence: 1, event_type: 'm' }),
        frame({ event_id: 'c', sequence: 3, event_type: 'm' }),
      ]),
    );

    const { result } = renderHook(() => useCoordinationStream('sess-1'));
    await waitFor(() => expect(result.current.events).toHaveLength(3));
    expect(result.current.events.map((e) => e.event_id)).toEqual(['a', 'b', 'c']);
  });

  test('sends the auth header and the Last-Event-ID cursor', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(streamResponse([]));
    renderHook(() => useCoordinationStream('sess-9'));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain('/api/v1/coordination/sessions/sess-9/events');
    const headers = init.headers as Record<string, string>;
    expect(headers['X-API-Key']).toBe('coord-key');
    expect(headers['Last-Event-ID']).toBe('0');
  });

  test('sets error status when the stream response is not ok', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('boom', { status: 500, headers: { 'Content-Type': 'text/plain' } }),
    );
    const { result } = renderHook(() => useCoordinationStream('sess-err'));
    await waitFor(() => expect(result.current.status).toBe('error'));
  });
});
