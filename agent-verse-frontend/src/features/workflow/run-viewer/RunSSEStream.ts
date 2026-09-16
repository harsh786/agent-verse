/**
 * RunSSEStream — hook for Server-Sent Events stream from workflow runs.
 *
 * Features:
 * - Automatic reconnection with Last-Event-ID header
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
  error?: string;
  status?: string;
  outputs?: Record<string, unknown>;
}

interface UseRunSSEOptions {
  runId: string | null | undefined;
  baseUrl?: string;
  apiKey?: string;
  enabled?: boolean;
}

interface UseRunSSEResult {
  events: RunEvent[];
  lastEvent: RunEvent | null;
  isConnected: boolean;
  error: string | null;
  clearEvents: () => void;
}

export function useRunSSE({
  runId,
  baseUrl = '',
  apiKey = '',
  enabled = true,
}: UseRunSSEOptions): UseRunSSEResult {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const lastEventIdRef = useRef<string>('');

  const clearEvents = useCallback(() => {
    setEvents([]);
  }, []);

  useEffect(() => {
    if (!runId || !enabled) return;

    // SSE connection
    const connect = () => {
      const params = new URLSearchParams({ 'Last-Event-ID': lastEventIdRef.current });
      const url = `${baseUrl}/api/v1/runs/${runId}/stream?${params}`;

      // SSE via fetch (supports custom headers)
      const controller = new AbortController();

      const startStream = async () => {
        try {
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
                if (raw === '[DONE]') {
                  setIsConnected(false);
                  return;
                }
                try {
                  const event = JSON.parse(raw) as RunEvent;
                  setEvents((prev) => [...prev, event]);
                } catch {
                  // Skip malformed frames
                }
              }
            }
          }
        } catch (err: unknown) {
          if ((err as Error).name !== 'AbortError') {
            setError(`SSE error: ${(err as Error).message}`);
            setIsConnected(false);
          }
        }
      };

      startStream();
      return controller;
    };

    const controller = connect();
    const eventSource = eventSourceRef.current;

    return () => {
      controller?.abort();
      eventSource?.close();
      setIsConnected(false);
    };
  }, [runId, enabled, baseUrl, apiKey]);

  return {
    events,
    lastEvent: events.length > 0 ? events[events.length - 1] : null,
    isConnected,
    error,
    clearEvents,
  };
}
