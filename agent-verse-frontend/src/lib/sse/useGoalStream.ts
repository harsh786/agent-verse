/**
 * SSE hook for real-time goal execution events.
 * Uses fetch-based streaming to support X-API-Key header.
 * Native EventSource cannot set custom headers, so we use fetch + ReadableStream.
 *
 * Reconnect behaviour: on unexpected close or network error the hook retries
 * with exponential backoff (1s, 2s, 4s, 8s, 16s, 30s, 30s, 30s) up to 8 attempts.
 * Retries are cancelled on terminal events (goal_complete / goal_failed /
 * goal_cancelled) and on component unmount.
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
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  onEventRef.current = opts?.onEvent;

  useEffect(() => {
    if (!goalId) return;

    // Reset retry counter whenever we connect to a new goal
    retryCountRef.current = 0;

    const apiKey =
      sessionStorage.getItem("av_api_key") ??
      localStorage.getItem("av_api_key") ??
      "";
    const API_BASE_URL =
      (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
    const url = `${API_BASE_URL}/goals/${goalId}/stream`;

    // scheduleReconnect and startConnection are mutually recursive; both are
    // defined before use via hoisting of the async function declaration.
    const scheduleReconnect = () => {
      if (retryCountRef.current >= 8) {
        setConnected(false);
        return;
      }
      const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
      retryCountRef.current += 1;
      retryTimerRef.current = setTimeout(() => {
        void startConnection();
      }, delay);
    };

    const startConnection = async () => {
      // Create a fresh AbortController for each attempt
      const abort = new AbortController();
      abortRef.current = abort;

      let terminalReceived = false;

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
          scheduleReconnect();
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

                const etype = parsed.type;
                if (
                  etype === "goal_complete" ||
                  etype === "goal_failed" ||
                  etype === "goal_cancelled"
                ) {
                  retryCountRef.current = 0; // Reset retries on terminal event
                  terminalReceived = true;
                  setConnected(false);
                }
              } catch {
                // ignore malformed JSON frames
              }
            }
          }
        }

        // Stream closed without a terminal event — schedule a reconnect
        if (!terminalReceived) {
          scheduleReconnect();
        }
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          // Network/fetch error — schedule a reconnect
          scheduleReconnect();
        }
      } finally {
        setConnected(false);
      }
    };

    void startConnection();

    return () => {
      // Cancel any pending retry timer and abort the in-flight request
      clearTimeout(retryTimerRef.current ?? undefined);
      abortRef.current?.abort();
      abortRef.current = null;
      setConnected(false);
    };
  }, [goalId]);

  return { events, connected };
}
