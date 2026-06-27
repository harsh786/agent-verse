/**
 * SSE hook for Civilization live events.
 * Mirrors useGoalStream with exponential backoff reconnect.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import type { CivilizationEvent } from '../api/civilizationApi';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

interface UseCivilizationStreamOptions {
  onEvent?: (event: CivilizationEvent) => void;
}

export function useCivilizationStream(
  civilizationId: string | null,
  options: UseCivilizationStreamOptions = {}
) {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<CivilizationEvent[]>([]);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const controllerRef = useRef<AbortController | undefined>(undefined);
  const onEventRef = useRef(options.onEvent);
  onEventRef.current = options.onEvent;

  const connect = useCallback(async () => {
    if (!civilizationId) return;

    const apiKey =
      sessionStorage.getItem('av_api_key') ?? localStorage.getItem('av_api_key') ?? '';
    const url = `${API_BASE}/civilizations/${civilizationId}/stream`;

    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;

    try {
      const resp = await fetch(url, {
        headers: { 'X-API-Key': apiKey, Accept: 'text/event-stream' },
        signal: controller.signal,
      });

      if (!resp.ok || !resp.body) {
        throw new Error(`SSE error ${resp.status}`);
      }

      setConnected(true);
      retryCountRef.current = 0;

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const evt = JSON.parse(line.slice(6)) as CivilizationEvent;
              setEvents(prev => [...prev.slice(-200), evt]); // keep last 200
              onEventRef.current?.(evt);
            } catch {
              // ignore parse errors
            }
          }
        }
      }
    } catch (err: unknown) {
      if ((err as Error)?.name === 'AbortError') return;
      setConnected(false);

      // Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s (max 8 retries)
      if (retryCountRef.current < 8) {
        const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
        retryCountRef.current += 1;
        retryTimerRef.current = setTimeout(() => void connect(), delay);
      }
    }
  }, [civilizationId]);

  useEffect(() => {
    void connect();
    return () => {
      controllerRef.current?.abort();
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
    };
  }, [connect]);

  return { connected, events };
}
