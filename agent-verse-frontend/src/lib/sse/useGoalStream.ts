/**
 * SSE hook for real-time goal execution events.
 * Connects to GET /goals/{id}/stream and emits typed events.
 */

import { useEffect, useRef, useState } from "react";

export interface GoalEvent {
  type: string;
  step?: string;
  output?: string;
  success?: boolean;
  reason?: string;
  iteration?: number;
  [key: string]: unknown;
}

interface UseGoalStreamOptions {
  onEvent?: (event: GoalEvent) => void;
}

export function useGoalStream(goalId: string | null, opts?: UseGoalStreamOptions) {
  const [events, setEvents] = useState<GoalEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const onEventRef = useRef(opts?.onEvent);
  onEventRef.current = opts?.onEvent;

  useEffect(() => {
    if (!goalId) return;

    const apiKey = localStorage.getItem("av_api_key") ?? "";
    const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "/api";
    const url = `${baseUrl}/goals/${goalId}/stream?api_key=${encodeURIComponent(apiKey)}`;

    const es = new EventSource(url);
    esRef.current = es;

    es.onopen = () => setConnected(true);

    es.onmessage = (e: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(e.data) as GoalEvent;
        setEvents((prev) => [...prev, parsed]);
        onEventRef.current?.(parsed);
      } catch {
        // ignore malformed events
      }
    };

    es.onerror = () => {
      setConnected(false);
      es.close();
    };

    return () => {
      es.close();
      setConnected(false);
    };
  }, [goalId]);

  return { events, connected };
}
