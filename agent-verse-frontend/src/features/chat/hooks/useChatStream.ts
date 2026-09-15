/**
 * useChatStream — opens an EventSource for the chat SSE stream.
 *
 * Appends token events incrementally, handles all SSE event types,
 * reconnects with exponential backoff on a dropped connection (before the
 * goal completes), and cleans up on unmount or session change.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { chatApi } from '@/lib/api/chat';
import type { SSEEvent } from '../types/chat.types';

export interface StreamState {
  isStreaming: boolean;
  tokens: string;           // accumulated QA tokens
  reasoning: string;        // accumulated reasoning tokens
  currentEvent: SSEEvent | null;
  events: SSEEvent[];       // full ordered sequence of structural events
  error: string | null;
  /** True while a dropped connection is being retried (before goal completion). */
  reconnecting: boolean;
}

const INITIAL: StreamState = {
  isStreaming: false,
  tokens: '',
  reasoning: '',
  currentEvent: null,
  events: [],
  error: null,
  reconnecting: false,
};

// Reconnect policy: cap the number of retries, exponential backoff 500ms → 8s.
const MAX_RETRIES = 5;
const BASE_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 8000;

export function useChatStream(
  sessionId: string | undefined,
  onDone?: (finalTokens: string) => void,
) {
  const [state, setState] = useState<StreamState>(INITIAL);
  const esRef = useRef<EventSource | null>(null);
  const tokensRef = useRef('');
  const reasoningRef = useRef('');
  const eventsRef = useRef<SSEEvent[]>([]);
  const messageIdRef = useRef<string | null>(null);
  const retryRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set once the stream reaches a terminal state (done / server error) so a
  // late onerror does not trigger a pointless reconnect.
  const terminatedRef = useRef(false);

  const clearReconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  // Opens (or re-opens) the EventSource for the current message. Extracted so
  // the reconnect path can call it again with the same URL/message id.
  const open = useCallback(() => {
    if (!sessionId || !messageIdRef.current) return;

    const url = chatApi.streamUrl(sessionId, messageIdRef.current);
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e: MessageEvent) => {
      try {
        const event: SSEEvent = JSON.parse(e.data as string);

        // A message arrived → the connection is healthy again; reset the
        // backoff counter so a future drop starts from a short delay.
        retryRef.current = 0;

        // Accumulate the ordered sequence of STRUCTURAL events (plan/step/tool/
        // knowledge/hitl/…) for the execution timeline; skip high-frequency
        // token/reasoning deltas which are already folded into tokens/reasoning.
        if (event.type !== 'token' && event.type !== 'reasoning') {
          eventsRef.current = [...eventsRef.current, event];
        }
        const events = eventsRef.current;

        if (event.type === 'token') {
          tokensRef.current += (event.token as string) ?? '';
          setState((prev) => ({ ...prev, tokens: tokensRef.current, currentEvent: event, reconnecting: false }));
        } else if (event.type === 'reasoning') {
          reasoningRef.current += (event.token as string) ?? '';
          setState((prev) => ({ ...prev, reasoning: reasoningRef.current, currentEvent: event, reconnecting: false }));
        } else if (event.type === 'done') {
          terminatedRef.current = true;
          clearReconnect();
          es.close();
          esRef.current = null;
          setState((prev) => ({ ...prev, isStreaming: false, reconnecting: false, currentEvent: event, events }));
          onDone?.(tokensRef.current);
        } else if (event.type === 'error') {
          terminatedRef.current = true;
          clearReconnect();
          es.close();
          esRef.current = null;
          setState((prev) => ({
            ...prev,
            isStreaming: false,
            reconnecting: false,
            error: (event.message as string) ?? 'Stream error',
            currentEvent: event,
            events,
          }));
        } else {
          // step_started/step_complete/tool_call/knowledge_retrieved/hitl_required/…
          setState((prev) => ({ ...prev, currentEvent: event, events, reconnecting: false }));
        }
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      es.close();
      esRef.current = null;

      // Terminal already reached → nothing to reconnect.
      if (terminatedRef.current) return;

      // Exhausted the retry budget → surface a hard error.
      if (retryRef.current >= MAX_RETRIES) {
        clearReconnect();
        setState((prev) => ({ ...prev, isStreaming: false, reconnecting: false, error: 'Connection error' }));
        return;
      }

      const backoff = Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** retryRef.current);
      retryRef.current += 1;
      // Keep isStreaming true so the UI shows an in-progress (reconnecting) state
      // rather than a hard failure while we retry.
      setState((prev) => ({ ...prev, isStreaming: true, reconnecting: true, error: null }));
      clearReconnect();
      reconnectTimerRef.current = setTimeout(() => {
        open();
      }, backoff);
    };
  }, [sessionId, onDone, clearReconnect]);

  const startStream = useCallback(
    (messageId: string) => {
      if (!sessionId) return;

      // Close any existing stream / pending reconnect.
      clearReconnect();
      esRef.current?.close();
      tokensRef.current = '';
      reasoningRef.current = '';
      eventsRef.current = [];
      messageIdRef.current = messageId;
      retryRef.current = 0;
      terminatedRef.current = false;

      setState({
        isStreaming: true,
        tokens: '',
        reasoning: '',
        currentEvent: null,
        events: [],
        error: null,
        reconnecting: false,
      });

      open();
    },
    [sessionId, open, clearReconnect],
  );

  const stopStream = useCallback(() => {
    terminatedRef.current = true;
    clearReconnect();
    esRef.current?.close();
    esRef.current = null;
    setState((prev) => ({ ...prev, isStreaming: false, reconnecting: false }));
  }, [clearReconnect]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      clearReconnect();
      esRef.current?.close();
    };
  }, [clearReconnect]);

  return { ...state, startStream, stopStream };
}
