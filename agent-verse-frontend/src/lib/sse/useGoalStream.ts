/**
 * SSE hook for real-time goal execution events.
 * Uses fetch-based streaming to support X-API-Key header.
 * Native EventSource cannot set custom headers, so we use fetch + ReadableStream.
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
  const abortRef = useRef<AbortController | null>(null);
  const onEventRef = useRef(opts?.onEvent);
  onEventRef.current = opts?.onEvent;

  useEffect(() => {
    if (!goalId) return;

    const apiKey = sessionStorage.getItem("av_api_key")
      ?? localStorage.getItem("av_api_key")
      ?? "";
    const API_BASE_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
    const url = `${API_BASE_URL}/goals/${goalId}/stream`;

    const abort = new AbortController();
    abortRef.current = abort;

    const connect = async () => {
      try {
        const res = await fetch(url, {
          headers: {
            "X-API-Key": apiKey,
            Accept: "text/event-stream",
          },
          signal: abort.signal,
        });

        if (!res.ok || !res.body) {
          setConnected(false);
          return;
        }

        setConnected(true);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames are separated by double newlines
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";

          for (const frame of frames) {
            for (const line of frame.split("\n")) {
              const data = line.startsWith("data: ") ? line.slice(6).trim() : null;
              if (!data) continue;
              try {
                const parsed = JSON.parse(data) as GoalEvent;
                setEvents((prev) => [...prev, parsed]);
                onEventRef.current?.(parsed);
              } catch {
                // ignore malformed JSON frames
              }
            }
          }
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setConnected(false);
        }
      } finally {
        setConnected(false);
      }
    };

    connect();

    return () => {
      abort.abort();
      abortRef.current = null;
      setConnected(false);
    };
  }, [goalId]);

  return { events, connected };
}
