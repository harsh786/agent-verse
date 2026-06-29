import { useEffect, useRef, useCallback } from 'react';

interface UseCollabSocketOptions {
  sessionId: string;
  apiKey: string;
  onMessage: (data: unknown) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (error: Event) => void;
  /** Set to false to disable reconnection (e.g. when session is closed intentionally) */
  reconnect?: boolean;
}

const MAX_RETRIES = 8;
const PING_INTERVAL_MS = 30_000;

export function useCollabSocket({
  sessionId,
  apiKey,
  onMessage,
  onOpen,
  onClose,
  onError,
  reconnect = true,
}: UseCollabSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const intentionalCloseRef = useRef(false);

  // Stable callback refs — always current without adding to effect deps
  const onMessageRef = useRef(onMessage);
  const onOpenRef = useRef(onOpen);
  const onCloseRef = useRef(onClose);
  const onErrorRef = useRef(onError);
  useEffect(() => {
    onMessageRef.current = onMessage;
    onOpenRef.current = onOpen;
    onCloseRef.current = onClose;
    onErrorRef.current = onError;
  });

  function encodeProtocolToken(value: string): string {
    const encoded = btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
    return `av.v1.${encoded}`;
  }

  function clearPing() {
    if (pingTimerRef.current) {
      clearInterval(pingTimerRef.current);
      pingTimerRef.current = null;
    }
  }

  const connect = useCallback(() => {
    const wsBase = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000';
    const url = `${wsBase}/collab/sessions/${sessionId}/ws`;
    const ws = new WebSocket(url, [encodeProtocolToken(apiKey)]);
    wsRef.current = ws;

    ws.onopen = () => {
      retryRef.current = 0; // reset backoff on successful connect
      onOpenRef.current?.();
      // Start heartbeat ping
      clearPing();
      pingTimerRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, PING_INTERVAL_MS);
    };

    ws.onmessage = (e: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(e.data);
        // Ignore pong messages
        if (parsed?.type === 'pong') return;
        onMessageRef.current(parsed);
      } catch {
        onMessageRef.current(e.data);
      }
    };

    ws.onerror = (e) => {
      onErrorRef.current?.(e);
    };

    ws.onclose = () => {
      clearPing();
      onCloseRef.current?.();
      if (!intentionalCloseRef.current && reconnect && retryRef.current < MAX_RETRIES) {
        const delay = Math.min(1000 * 2 ** retryRef.current, 30_000);
        retryRef.current += 1;
        retryTimerRef.current = setTimeout(() => connect(), delay);
      }
    };

    return ws;
  }, [sessionId, apiKey, reconnect]);

  const sendMessage = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    intentionalCloseRef.current = false;
    connect();
    return () => {
      intentionalCloseRef.current = true;
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      clearPing();
      wsRef.current?.close();
    };
  }, [connect]);

  return { sendMessage };
}
