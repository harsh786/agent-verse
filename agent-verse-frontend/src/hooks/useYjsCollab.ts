/**
 * useYjsCollab — Yjs CRDT document for real-time collaborative editing.
 *
 * Uses WebSocket provider (y-websocket) to sync document state across clients.
 * Falls back gracefully to local-only editing if WS is unavailable.
 *
 * Authentication: prefers a short-lived CRDT token (POST /collab/crdt-token)
 * over the long-lived API key, reducing exposure of the permanent credential.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import type { Awareness } from 'y-protocols/awareness';
import { useAuthStore } from '@/stores/auth';
import { collabApi } from '@/lib/api/client';

export interface CursorInfo {
  clientId: number;
  name: string;
  color: string;
  position: number; // caret position in the text
  selection?: { anchor: number; head: number };
}

export interface UseYjsCollabOptions {
  roomId: string;     // unique room identifier (e.g. session_id)
  userName?: string;  // display name for this user's cursor
  color?: string;     // cursor color
}

export interface UseYjsCollabReturn {
  text: string;
  setText: (newText: string, origin?: string) => void;
  updateCursorPosition: (position: number, selection?: { anchor: number; head: number }) => void;
  cursors: CursorInfo[];
  connected: boolean;
  synced: boolean;
  awareness: Awareness | null;
  undoManager: Y.UndoManager | null;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

const CURSOR_COLORS = [
  '#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7',
  '#DDA0DD', '#98D8C8', '#F7DC6F', '#BB8FCE', '#85C1E9',
];

export function useYjsCollab({
  roomId,
  userName,
  color,
}: UseYjsCollabOptions): UseYjsCollabReturn {
  const { apiKey, tenantId } = useAuthStore();
  const docRef = useRef<Y.Doc | null>(null);
  const providerRef = useRef<WebsocketProvider | null>(null);
  const undoManagerRef = useRef<Y.UndoManager | null>(null);
  const [text, setText_state] = useState('');
  const [cursors, setCursors] = useState<CursorInfo[]>([]);
  const [connected, setConnected] = useState(false);
  const [synced, setSynced] = useState(false);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);

  const clientColor = useRef(
    color ?? CURSOR_COLORS[Math.floor(Math.random() * CURSOR_COLORS.length)],
  );

  // Keep awareness ref stable for the return value
  const awarenessRef = useRef<Awareness | null>(null);

  useEffect(() => {
    if (!roomId) return;

    // Create Yjs document
    const doc = new Y.Doc();
    docRef.current = doc;
    const yText = doc.getText('content');

    // Undo manager scoped to content
    const undoManager = new Y.UndoManager(yText, { captureTimeout: 500 });
    undoManagerRef.current = undoManager;

    // Update undo/redo state on every stack change
    const updateUndoState = () => {
      setCanUndo(undoManager.canUndo());
      setCanRedo(undoManager.canRedo());
    };
    undoManager.on('stack-cleared', updateUndoState);
    undoManager.on('stack-item-added', updateUndoState);
    undoManager.on('stack-item-updated', updateUndoState);
    undoManager.on('stack-item-popped', updateUndoState);

    // Observe text changes
    const onTextChange = () => {
      setText_state(yText.toString());
    };
    yText.observe(onTextChange);

    // Track whether the effect has been cleaned up (avoids state updates after unmount)
    let cancelled = false;

    const setupProvider = async () => {
      // Build WebSocket URL — prefer VITE_WS_URL for consistency with useCollabSocket
      const wsBase =
        (import.meta.env.VITE_WS_URL as string | undefined) ??
        (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/^http/, 'ws') ??
        'ws://localhost:8000';
      // y-websocket appends '/{roomName}' to serverUrl automatically
      const wsUrl = `${wsBase}/collab/crdt`;
      const roomName = `collab-${tenantId || 'default'}-${roomId}`;

      // Prefer a short-lived CRDT token — falls back to the long-lived API key
      let wsParams: Record<string, string> = {};
      try {
        const { token } = await collabApi.getCrdtToken();
        wsParams = { token };
      } catch {
        // Token fetch failed (e.g. backend not yet upgraded) — fall back to API key
        if (apiKey) wsParams = { api_key: apiKey };
      }

      if (cancelled) return;

      let provider: WebsocketProvider | null = null;
      try {
        provider = new WebsocketProvider(wsUrl, roomName, doc, { params: wsParams });
        providerRef.current = provider;
        awarenessRef.current = provider.awareness;

        provider.on('status', ({ status: s }: { status: string }) => {
          if (!cancelled) setConnected(s === 'connected');
        });
        provider.on('sync', (isSynced: boolean) => {
          if (!cancelled) setSynced(isSynced);
        });

        // Awareness (cursor presence)
        const awareness = provider.awareness;
        awareness.setLocalStateField('user', {
          name: userName ?? `User-${doc.clientID.toString(36).slice(-4)}`,
          color: clientColor.current,
          clientId: doc.clientID,
        });

        type AwarenessState = Record<string, unknown>;
        type CursorField = { position?: number; selection?: { anchor: number; head: number } };
        type UserField = { name?: string; color?: string };

        const onAwarenessChange = () => {
          const states = Array.from(awareness.getStates().entries())
            .filter(([clientId]) => clientId !== doc.clientID)
            .map(([clientId, state]) => {
              const s = state as AwarenessState;
              const user = (s.user ?? {}) as UserField;
              const cursor = (s.cursor ?? {}) as CursorField;
              return {
                clientId,
                name: user.name ?? `User ${clientId}`,
                color: user.color ?? '#888',
                position: cursor.position ?? 0,
                selection: cursor.selection,
              };
            });
          if (!cancelled) setCursors(states);
        };
        awareness.on('change', onAwarenessChange);
      } catch {
        // WS failed — fall back to local-only editing
        if (!cancelled) setConnected(false);
      }
    };

    void setupProvider();

    return () => {
      cancelled = true;
      yText.unobserve(onTextChange);
      undoManager.off('stack-cleared', updateUndoState);
      undoManager.off('stack-item-added', updateUndoState);
      undoManager.off('stack-item-updated', updateUndoState);
      undoManager.off('stack-item-popped', updateUndoState);
      providerRef.current?.destroy();
      doc.destroy();
      docRef.current = null;
      providerRef.current = null;
      awarenessRef.current = null;
      undoManagerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomId, apiKey, tenantId, userName]);

  const setText = useCallback((newText: string, origin?: string) => {
    const doc = docRef.current;
    if (!doc) return;
    const yText = doc.getText('content');
    const current = yText.toString();
    if (current === newText) return;
    // Replace entire content in a single transaction for textarea-driven edits
    doc.transact(() => {
      yText.delete(0, yText.length);
      yText.insert(0, newText);
    }, origin ?? 'local');
  }, []);

  const updateCursorPosition = useCallback(
    (position: number, selection?: { anchor: number; head: number }) => {
      providerRef.current?.awareness.setLocalStateField('cursor', { position, selection });
    },
    [],
  );

  const undo = useCallback(() => {
    undoManagerRef.current?.undo();
  }, []);

  const redo = useCallback(() => {
    undoManagerRef.current?.redo();
  }, []);

  return {
    text,
    setText,
    updateCursorPosition,
    cursors,
    connected,
    synced,
    awareness: awarenessRef.current,
    undoManager: undoManagerRef.current,
    undo,
    redo,
    canUndo,
    canRedo,
  };
}
