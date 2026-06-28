/**
 * Generic reusable SSE hook. A path-generic version of useGoalStream:
 * fetch + X-API-Key (native EventSource cannot set headers), ReadableStream
 * reader, frames split on "\n\n", exponential backoff (1s..30s, max 8 attempts),
 * retries cancelled on terminalTypes events and unmount.
 */
import { useEffect, useRef, useState } from "react";

export interface StreamEvent {
  type: string;
  [key: string]: unknown;
}

export interface UseEventStreamOptions {
  onEvent?: (e: StreamEvent) => void;
  terminalTypes?: string[];
  enabled?: boolean;
}

const MAX_RETRIES = 8;
const MAX_BACKOFF_MS = 30000;

export function useEventStream(
  path: string | null,
  opts?: UseEventStreamOptions,
): { events: StreamEvent[]; connected: boolean } {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const onEventRef = useRef(opts?.onEvent);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  onEventRef.current = opts?.onEvent;
  const terminalTypes = opts?.terminalTypes ?? [];
  const enabled = opts?.enabled ?? true;

  useEffect(() => {
    if (!path || !enabled) return;
    retryCountRef.current = 0;

    const apiKey =
      sessionStorage.getItem("av_api_key") ??
      localStorage.getItem("av_api_key") ??
      "";
    const API_BASE_URL =
      (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
    const url = `${API_BASE_URL}${path}`;

    const scheduleReconnect = () => {
      if (retryCountRef.current >= MAX_RETRIES) {
        setConnected(false);
        return;
      }
      const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), MAX_BACKOFF_MS);
      retryCountRef.current += 1;
      retryTimerRef.current = setTimeout(() => void startConnection(), delay);
    };

    const startConnection = async () => {
      const abort = new AbortController();
      abortRef.current = abort;
      let terminalReceived = false;
      try {
        const res = await fetch(url, {
          headers: { "X-API-Key": apiKey, Accept: "text/event-stream" },
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
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            for (const line of frame.split("\n")) {
              const data = line.startsWith("data: ") ? line.slice(6).trim() : null;
              if (!data) continue;
              try {
                const parsed = JSON.parse(data) as StreamEvent;
                setEvents((prev) => [...prev, parsed]);
                onEventRef.current?.(parsed);
                if (terminalTypes.includes(parsed.type)) {
                  retryCountRef.current = 0;
                  terminalReceived = true;
                  setConnected(false);
                }
              } catch {
                // ignore malformed JSON frames
              }
            }
          }
        }
        if (!terminalReceived) scheduleReconnect();
      } catch (err) {
        if ((err as Error).name !== "AbortError") scheduleReconnect();
      } finally {
        setConnected(false);
      }
    };

    void startConnection();
    return () => {
      clearTimeout(retryTimerRef.current);
      abortRef.current?.abort();
      abortRef.current = null;
      setConnected(false);
    };
    // terminalTypes/enabled are captured per-effect; path drives reconnection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled]);

  return { events, connected };
}
