/**
 * RunSSEStream — hook for Server-Sent Events stream from workflow runs.
 *
 * Features:
 * - Live run status: run_status / run_waiting / heartbeat events
 * - A stream that closes before the run is terminal is NOT a failure: the hook
 *   reconnects (with the Last-Event-ID) until run_completed / run_failed /
 *   run_cancelled arrives
 * - Typed event payloads
 * - Cleanup on unmount
 * - Error + loading state
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { getAuthHeader } from '@/stores/auth';

export interface RunEvent {
  event: string;
  step_id?: string;
  step_type?: string;
  output_keys?: string[];
  error?: string | null;
  error_step_id?: string | null;
  status?: string;
  outputs?: Record<string, unknown>;
}

/** Events after which the run will not change again. */
export const TERMINAL_RUN_EVENTS = new Set(['run_completed', 'run_failed', 'run_cancelled']);

interface UseRunSSEOptions {
  runId: string | null | undefined;
  baseUrl?: string;
  apiKey?: string;
  enabled?: boolean;
  /** Delay before reconnecting a stream that closed before the run finished. */
  reconnectDelayMs?: number;
}

interface UseRunSSEResult {
  events: RunEvent[];
  lastEvent: RunEvent | null;
  isConnected: boolean;
  /** Latest run status reported by the stream (e.g. running, waiting_hitl). */
  status: string | null;
  /** True once a terminal run event arrived. */
  isTerminal: boolean;
  error: string | null;
  clearEvents: () => void;
}

export function useRunSSE({
  runId,
  baseUrl = '',
  apiKey = '',
  enabled = true,
  reconnectDelayMs = 2000,
}: UseRunSSEOptions): UseRunSSEResult {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [isTerminal, setIsTerminal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastEventIdRef = useRef<string>('');

  const clearEvents = useCallback(() => {
    setEvents([]);
  }, []);

  useEffect(() => {
    if (!runId || !enabled) return;

    let cancelled = false;
    let terminal = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const scheduleReconnect = () => {
      if (cancelled || terminal) return;
      timer = setTimeout(startStream, reconnectDelayMs);
    };

    const handle = (event: RunEvent) => {
      if (event.status) setStatus(event.status);
      if (TERMINAL_RUN_EVENTS.has(event.event)) {
        terminal = true;
        setIsTerminal(true);
      }
      // Heartbeats only keep the connection alive; they are not run history.
      if (event.event !== 'heartbeat') setEvents((prev) => [...prev, event]);
    };

    async function startStream() {
      if (cancelled) return;
      controller = new AbortController();
      const params = new URLSearchParams({ 'Last-Event-ID': lastEventIdRef.current });
      const url = `${baseUrl}/api/v1/runs/${runId}/stream?${params}`;
      try {
        // SSE via fetch (supports custom headers)
        const resp = await fetch(url, {
          headers: {
            ...getAuthHeader(),
            Accept: 'text/event-stream',
          },
          signal: controller.signal,
        });

        if (!resp.ok) {
          setError(`SSE failed: ${resp.status}`);
          setIsConnected(false);
          // Server errors are retried; a 4xx (not found / forbidden) is final.
          if (resp.status >= 500) scheduleReconnect();
          return;
        }

        setIsConnected(true);
        setError(null);

        const reader = resp.body?.getReader();
        if (!reader) return;

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            if (line.startsWith('id: ')) {
              lastEventIdRef.current = line.slice(4);
            } else if (line.startsWith('data: ')) {
              const raw = line.slice(6);
              if (raw === '[DONE]') continue;
              try {
                handle(JSON.parse(raw) as RunEvent);
              } catch {
                // Skip malformed frames
              }
            }
          }
        }
        setIsConnected(false);
        // Closed before the run finished (e.g. stream_timeout, proxy idle
        // cut-off): keep watching instead of treating it as a failure.
        scheduleReconnect();
      } catch (err: unknown) {
        if ((err as Error).name === 'AbortError') return;
        setError(`SSE error: ${(err as Error).message}`);
        setIsConnected(false);
        scheduleReconnect();
      }
    }

    startStream();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
      setIsConnected(false);
    };
  }, [runId, enabled, baseUrl, apiKey, reconnectDelayMs]);

  return {
    events,
    lastEvent: events.length > 0 ? events[events.length - 1] : null,
    isConnected,
    status,
    isTerminal,
    error,
    clearEvents,
  };
}
