/**
 * SSE hook for real-time goal execution events.
 * Uses fetch-based streaming to support X-API-Key header.
 * Native EventSource cannot set custom headers, so we use fetch + ReadableStream.
 *
 * Reconnect behaviour: on unexpected close or network error the hook retries
 * with exponential backoff (1s, 2s, 4s, 8s, 16s, 30s, 30s…) up to 100 attempts.
 * This supports goals that run for 1+ hours without losing the live feed.
 * Retries are cancelled on terminal events (goal_complete / goal_failed /
 * goal_cancelled) and on component unmount.
 *
 * Auth error short-circuit: 401 / 403 responses immediately invoke logout() and
 * do NOT retry — retrying against an expired/invalid token would create a storm
 * of requests that will never succeed.
 *
 * Token streaming:
 * - `token_chunk` events update `streamingToken` with the step name and cumulative text.
 * - `step_complete` / `step_completed` events clear `streamingToken` (the LLM finished).
 * - `streamingToken` is null when no streaming is in progress.
 */

import { useEffect, useRef, useState } from "react";
import { useAuthStore } from "@/stores/auth";

export interface GoalEvent {
  type: string;
  step?: string;
  output?: string;
  success?: boolean;
  reason?: string;
  iteration?: number;
  [key: string]: unknown;
}

/** Live state of the currently-streaming LLM output for a single step. */
export interface StreamingToken {
  /** Step description identifying which step is being executed. */
  step: string;
  /** Full text accumulated so far (all token chunks joined). */
  cumulative: string;
}

interface UseGoalStreamOptions {
  onEvent?: (event: GoalEvent) => void;
  reconnectKey?: number;
}

export function useGoalStream(goalId: string | null, opts?: UseGoalStreamOptions) {
  const [events, setEvents] = useState<GoalEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [streamingToken, setStreamingToken] = useState<StreamingToken | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const onEventRef = useRef(opts?.onEvent);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Track the last SSE event ID so reconnects can resume without gaps.
  // The backend can emit "id: <value>" lines; when present the browser stores
  // lastEventId on the MessageEvent.  With a fetch-based reader we extract it
  // from the raw frame ("id: " prefix) and store it here.
  const lastEventIdRef = useRef<string>('');
  const reconnectKey = opts?.reconnectKey;

  onEventRef.current = opts?.onEvent;

  useEffect(() => {
    if (!goalId) return;

    // Reset retry counter, streaming state, and last-event-id whenever we
    // connect to a new goal.
    retryCountRef.current = 0;
    lastEventIdRef.current = '';
    setStreamingToken(null);

    const API_BASE_URL =
      (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";
    const url = `${API_BASE_URL}/goals/${goalId}/stream`;

    // scheduleReconnect and startConnection are mutually recursive; both are
    // defined before use via hoisting of the async function declaration.
    const scheduleReconnect = () => {
      if (retryCountRef.current >= 100) {
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

      // Read the auth token fresh on each (re)connect attempt — not captured
      // once at mount — so a token rotation between retries is picked up.
      const { apiKey: storeKey, ssoMode, accessToken } = useAuthStore.getState();
      const apiKey =
        storeKey ||
        sessionStorage.getItem("av_api_key") ||
        localStorage.getItem("av_api_key") ||
        "";

      const authHeaders: Record<string, string> = ssoMode && accessToken
        ? { Authorization: `Bearer ${accessToken}` }
        : apiKey
        ? { "X-API-Key": apiKey }
        : {};

      // Attach Last-Event-ID so the backend can replay missed events on
      // reconnect (RFC 6202 / SSE spec).  Only sent when we have a previous ID.
      const resumeHeaders: Record<string, string> = lastEventIdRef.current
        ? { "Last-Event-ID": lastEventIdRef.current }
        : {};

      let terminalReceived = false;

      try {
        const res = await fetch(url, {
          headers: {
            ...authHeaders,
            ...resumeHeaders,
            Accept: "text/event-stream",
          },
          signal: abort.signal,
        });

        if (!res.ok || !res.body) {
          setConnected(false);
          // 401/403 — auth failure. Stop retrying immediately; every retry will
          // also get a 401/403, creating a storm of failing requests.
          if (res.status === 401 || res.status === 403) {
            useAuthStore.getState().logout();
            return;
          }
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
            // Extract SSE "id:" field from the frame before parsing data lines.
            // RFC 6202: an "id" field sets the last event ID for the stream.
            for (const line of frame.split("\n")) {
              if (line.startsWith("id: ")) {
                const id = line.slice(4).trim();
                if (id) lastEventIdRef.current = id;
              }
            }

            for (const line of frame.split("\n")) {
              const data = line.startsWith("data: ") ? line.slice(6).trim() : null;
              if (!data) continue;
              try {
                const parsed = JSON.parse(data) as GoalEvent;

                const etype = parsed.type;

                // token_chunk — update streaming state, do NOT push to events array
                if (etype === "token_chunk") {
                  const step = (parsed.step as string | undefined) ?? "";
                  const cumulative = (parsed.cumulative as string | undefined) ?? "";
                  setStreamingToken({ step, cumulative });
                  onEventRef.current?.(parsed);
                  continue;
                }

                // Structural events — push to events array with dedup + cap
                setEvents((prev) => {
                  const eventId = parsed["event_id"] as string | undefined;
                  if (eventId && prev.some((e) => (e["event_id"] as string | undefined) === eventId)) {
                    return prev; // duplicate — skip
                  }
                  return [...prev, parsed].slice(-1000); // cap at 1000 events
                });
                onEventRef.current?.(parsed);

                // A step completing clears any in-progress streaming display
                if (etype === "step_complete" || etype === "step_completed") {
                  setStreamingToken(null);
                }

                if (
                  etype === "goal_complete" ||
                  etype === "goal_failed" ||
                  etype === "goal_cancelled"
                ) {
                  retryCountRef.current = 0; // Reset retries on terminal event
                  terminalReceived = true;
                  setConnected(false);
                  setStreamingToken(null);
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
      setStreamingToken(null);
    };
  }, [goalId, reconnectKey]);

  return { events, connected, streamingToken };
}
