import { useEffect, useRef, useCallback } from 'react';

interface UseCollabSocketOptions {
  sessionId: string;
  apiKey: string;
  onMessage: (data: unknown) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

export function useCollabSocket({
  sessionId,
  apiKey,
  onMessage,
  onOpen,
  onClose,
}: UseCollabSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  const onOpenRef = useRef(onOpen);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onMessageRef.current = onMessage;
    onOpenRef.current = onOpen;
    onCloseRef.current = onClose;
  }, [onMessage, onOpen, onClose]);

  function encodeProtocolToken(value: string): string {
    const encoded = btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
    return `av.v1.${encoded}`;
  }

  const connect = useCallback(() => {
    const wsBase = ((import.meta as any).env?.VITE_WS_URL ?? 'ws://localhost:8000');
    const url = `${wsBase}/collab/sessions/${sessionId}/ws`;
    const ws = new WebSocket(url, [encodeProtocolToken(apiKey)]);
    wsRef.current = ws;
    ws.onopen = () => onOpenRef.current?.();
    ws.onclose = () => onCloseRef.current?.();
    ws.onmessage = (e: MessageEvent<string>) => {
      try {
        onMessageRef.current(JSON.parse(e.data));
      } catch {
        onMessageRef.current(e.data);
      }
    };
    return ws;
  }, [sessionId, apiKey]);

  const sendMessage = useCallback((data: unknown) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  useEffect(() => {
    const ws = connect();
    return () => ws.close();
  }, [connect]);

  return { sendMessage };
}
