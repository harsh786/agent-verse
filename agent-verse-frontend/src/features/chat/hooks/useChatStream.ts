/**
 * useChatStream — opens an EventSource for the chat SSE stream.
 *
 * Appends token events incrementally, handles all SSE event types,
 * and cleans up on unmount or session change.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { chatApi } from '@/lib/api/chat';
import type { SSEEvent } from '../types/chat.types';

export interface StreamState {
  isStreaming: boolean;
  tokens: string;           // accumulated QA tokens
  reasoning: string;        // accumulated reasoning tokens
  currentEvent: SSEEvent | null;
  error: string | null;
}

const INITIAL: StreamState = {
  isStreaming: false,
  tokens: '',
  reasoning: '',
  currentEvent: null,
  error: null,
};

export function useChatStream(
  sessionId: string | undefined,
  onDone?: (finalTokens: string) => void,
) {
  const [state, setState] = useState<StreamState>(INITIAL);
  const esRef = useRef<EventSource | null>(null);
  const tokensRef = useRef('');
  const reasoningRef = useRef('');

  const startStream = useCallback(
    (messageId: string) => {
      if (!sessionId) return;

      // Close any existing stream
      esRef.current?.close();
      tokensRef.current = '';
      reasoningRef.current = '';

      setState({ isStreaming: true, tokens: '', reasoning: '', currentEvent: null, error: null });

      const url = chatApi.streamUrl(sessionId, messageId);
      const es = new EventSource(url);
      esRef.current = es;

      es.onmessage = (e: MessageEvent) => {
        try {
          const event: SSEEvent = JSON.parse(e.data as string);

          if (event.type === 'token') {
            tokensRef.current += (event.token as string) ?? '';
            setState((prev) => ({ ...prev, tokens: tokensRef.current, currentEvent: event }));
          } else if (event.type === 'reasoning') {
            reasoningRef.current += (event.token as string) ?? '';
            setState((prev) => ({ ...prev, reasoning: reasoningRef.current, currentEvent: event }));
          } else if (event.type === 'done') {
            es.close();
            esRef.current = null;
            setState((prev) => ({ ...prev, isStreaming: false, currentEvent: event }));
            onDone?.(tokensRef.current);
          } else if (event.type === 'hitl_required') {
            // G-02: HITL required — surface to ChatPage via currentEvent
            setState((prev) => ({ ...prev, currentEvent: event }));
          } else if (event.type === 'error') {
            es.close();
            setState((prev) => ({
              ...prev,
              isStreaming: false,
              error: (event.message as string) ?? 'Stream error',
              currentEvent: event,
            }));
          } else {
            setState((prev) => ({ ...prev, currentEvent: event }));
          }
        } catch {
          // ignore parse errors
        }
      };

      es.onerror = () => {
        setState((prev) => ({ ...prev, isStreaming: false, error: 'Connection error' }));
        es.close();
        esRef.current = null;
      };
    },
    [sessionId, onDone],
  );

  const stopStream = useCallback(() => {
    esRef.current?.close();
    esRef.current = null;
    setState((prev) => ({ ...prev, isStreaming: false }));
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      esRef.current?.close();
    };
  }, []);

  return { ...state, startStream, stopStream };
}
