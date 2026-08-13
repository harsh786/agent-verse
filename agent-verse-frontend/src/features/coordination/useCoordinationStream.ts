import { useEffect, useRef, useState } from 'react';
import { API_BASE } from '@/lib/api/client';
import { getAuthHeader } from '@/stores/auth';
import type { CoordinationEvent } from './types';

type StreamStatus = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'closed' | 'error';

export function useCoordinationStream(sessionId: string) {
  const [events, setEvents] = useState<CoordinationEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>('idle');
  const cursor = useRef(0);

  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    let reconnects = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    async function connect() {
      setStatus(reconnects ? 'reconnecting' : 'connecting');
      try {
        const response = await fetch(
          `${API_BASE}/api/v1/coordination/sessions/${encodeURIComponent(sessionId)}/events`,
          {
            headers: { ...getAuthHeader(), 'Last-Event-ID': String(cursor.current) },
            signal: controller.signal,
          },
        );
        if (!response.ok || !response.body) throw new Error(`Stream failed: ${response.status}`);
        setStatus('live');
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let pending = '';
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read();
          if (done) break;
          pending += decoder.decode(value, { stream: true });
          const frames = pending.split(/\r?\n\r?\n/);
          pending = frames.pop() ?? '';
          for (const frame of frames) {
            const data = frame
              .split(/\r?\n/)
              .find((line) => line.startsWith('data: '))
              ?.slice(6);
            if (!data) continue;
            const event = JSON.parse(data) as CoordinationEvent;
            if (event.sequence <= cursor.current) continue;
            cursor.current = event.sequence;
            setEvents((prior) => [...prior.slice(-499), event]);
          }
        }
        if (!controller.signal.aborted) {
          reconnects += 1;
          reconnectTimer = setTimeout(connect, Math.min(5_000, 500 * 2 ** reconnects));
        }
      } catch {
        if (controller.signal.aborted) return;
        setStatus('error');
        reconnects += 1;
        reconnectTimer = setTimeout(connect, Math.min(5_000, 500 * 2 ** reconnects));
      }
    }

    void connect();
    return () => {
      controller.abort();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      setStatus('closed');
    };
  }, [sessionId]);

  return { events, status, lastSequence: cursor.current };
}
