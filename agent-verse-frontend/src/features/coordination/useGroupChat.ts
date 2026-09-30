import { useCallback, useEffect, useRef, useState } from 'react';
import { mfaSubprotocol, useAuthStore } from '@/stores/auth';
import type { CoordinationMessage } from './types';

type GroupChatStatus = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'closed';

type Frame =
  | { type: 'message' | 'ack'; message: CoordinationMessage }
  | { type: 'replay_complete'; last_sequence: number }
  | { type: 'pong' }
  | { type: 'error'; code: string };

const SUBPROTOCOL = 'agentverse.coordination.v1';
const MAX_BACKOFF_MS = 10_000;

function protocolToken(value: string): string {
  let binary = '';
  new TextEncoder().encode(value).forEach((b) => {
    binary += String.fromCharCode(b);
  });
  return `av.v1.${btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')}`;
}

/** Merge by message_id (falling back to sequence) and keep causal order. */
export function mergeMessages(
  prior: CoordinationMessage[],
  incoming: CoordinationMessage[],
): CoordinationMessage[] {
  const byId = new Map<string, CoordinationMessage>();
  for (const message of [...prior, ...incoming]) {
    byId.set(message.message_id ?? `seq:${message.sequence}`, message);
  }
  return [...byId.values()].sort((a, b) => a.sequence - b.sequence);
}

/**
 * Live group chat for one coordination session. Every participant's message
 * arrives as a `message` frame (the sender's own as an `ack`); frames are
 * de-duplicated by message id, and a reconnect replays from the last sequence.
 */
export function useGroupChat(sessionId: string) {
  const apiKey = useAuthStore((s) => s.apiKey);
  const accessToken = useAuthStore((s) => s.accessToken);
  const credential = apiKey || accessToken || '';
  const [messages, setMessages] = useState<CoordinationMessage[]>([]);
  const [status, setStatus] = useState<GroupChatStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const lastSequence = useRef(0);

  useEffect(() => {
    if (!sessionId || !credential) return;
    let closedByUs = false;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    lastSequence.current = 0;
    setMessages([]);

    const connect = () => {
      setStatus(attempts ? 'reconnecting' : 'connecting');
      const base = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000';
      const url =
        `${base}/api/v1/coordination/sessions/${encodeURIComponent(sessionId)}` +
        `/group-chat/ws?after_sequence=${lastSequence.current}`;
      const mfa = mfaSubprotocol();
      const protocols = [SUBPROTOCOL, protocolToken(credential), ...(mfa ? [mfa] : [])];
      const socket = new WebSocket(url, protocols);
      socketRef.current = socket;
      socket.onmessage = (event: MessageEvent<string>) => {
        let frame: Frame;
        try {
          frame = JSON.parse(event.data) as Frame;
        } catch {
          return;
        }
        if (frame.type === 'message' || frame.type === 'ack') {
          lastSequence.current = Math.max(lastSequence.current, frame.message.sequence);
          setMessages((prior) => mergeMessages(prior, [frame.message]));
        } else if (frame.type === 'replay_complete') {
          attempts = 0;
          lastSequence.current = Math.max(lastSequence.current, frame.last_sequence);
          setStatus('live');
          setError(null);
        } else if (frame.type === 'error') {
          setError(frame.code);
        }
      };
      socket.onclose = () => {
        socketRef.current = null;
        if (closedByUs) return;
        attempts += 1;
        setStatus('reconnecting');
        timer = setTimeout(connect, Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempts));
      };
    };

    connect();
    return () => {
      closedByUs = true;
      if (timer) clearTimeout(timer);
      socketRef.current?.close();
      socketRef.current = null;
      setStatus('closed');
    };
  }, [sessionId, credential]);

  const send = useCallback((content: string) => {
    const socket = socketRef.current;
    const text = content.trim();
    if (!socket || socket.readyState !== WebSocket.OPEN || !text) return false;
    socket.send(JSON.stringify({ type: 'message', content: text, client_message_id: crypto.randomUUID() }));
    return true;
  }, []);

  return { messages, status, error, send };
}
