/**
 * Tests for useYjsCollab — the Yjs CRDT collaboration hook.
 *
 * y-websocket opens a real WebSocket in its constructor, which jsdom can't
 * support in a unit test, so the module is mocked with a lightweight fake
 * provider that exposes the same on/off/awareness/destroy surface and lets
 * tests trigger 'status' / 'sync' / awareness 'change' events manually.
 * Yjs itself (Y.Doc, Y.UndoManager) is NOT mocked — it runs for real, so the
 * text/undo/redo logic is exercised against the genuine CRDT.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

// ── Fake y-websocket ─────────────────────────────────────────────────────────

const { FakeWebsocketProvider } = vi.hoisted(() => {
  class FakeAwareness {
    private listeners: Record<string, Array<(...args: unknown[]) => void>> = {};
    private states = new Map<number, Record<string, unknown>>();
    clientID: number;

    constructor(clientID: number) {
      this.clientID = clientID;
    }
    setLocalStateField(field: string, value: unknown) {
      const cur = this.states.get(this.clientID) ?? {};
      this.states.set(this.clientID, { ...cur, [field]: value });
      this.emit('change');
    }
    getStates() {
      return this.states;
    }
    on(event: string, cb: (...args: unknown[]) => void) {
      (this.listeners[event] ??= []).push(cb);
    }
    off(event: string, cb: (...args: unknown[]) => void) {
      this.listeners[event] = (this.listeners[event] ?? []).filter((f) => f !== cb);
    }
    emit(event: string, ...args: unknown[]) {
      (this.listeners[event] ?? []).forEach((cb) => cb(...args));
    }
    // test helper: inject a remote peer's state directly
    _setRemoteState(clientId: number, state: Record<string, unknown>) {
      this.states.set(clientId, state);
      this.emit('change');
    }
  }

  class FakeWebsocketProvider {
    static instances: FakeWebsocketProvider[] = [];
    static shouldThrow = false;
    url: string;
    roomName: string;
    doc: { clientID: number };
    opts: unknown;
    awareness: FakeAwareness;
    destroyed = false;
    private listeners: Record<string, Array<(...args: unknown[]) => void>> = {};

    constructor(url: string, roomName: string, doc: { clientID: number }, opts: unknown) {
      if (FakeWebsocketProvider.shouldThrow) {
        throw new Error('ws construction failed');
      }
      this.url = url;
      this.roomName = roomName;
      this.doc = doc;
      this.opts = opts;
      this.awareness = new FakeAwareness(doc.clientID);
      FakeWebsocketProvider.instances.push(this);
    }
    on(event: string, cb: (...args: unknown[]) => void) {
      (this.listeners[event] ??= []).push(cb);
    }
    off(event: string, cb: (...args: unknown[]) => void) {
      this.listeners[event] = (this.listeners[event] ?? []).filter((f) => f !== cb);
    }
    destroy() {
      this.destroyed = true;
    }
    _emit(event: string, ...args: unknown[]) {
      (this.listeners[event] ?? []).forEach((cb) => cb(...args));
    }
  }

  return { FakeWebsocketProvider };
});

type FakeWebsocketProviderT = InstanceType<typeof FakeWebsocketProvider>;

vi.mock('y-websocket', () => ({
  WebsocketProvider: FakeWebsocketProvider,
}));

const getCrdtToken = vi.fn();
vi.mock('@/lib/api/client', () => ({
  collabApi: {
    getCrdtToken: (...args: unknown[]) => getCrdtToken(...args),
  },
}));

import { useYjsCollab } from './useYjsCollab';

function latestProvider(): FakeWebsocketProviderT {
  const inst = FakeWebsocketProvider.instances[FakeWebsocketProvider.instances.length - 1];
  if (!inst) throw new Error('no provider created');
  return inst;
}

beforeEach(() => {
  FakeWebsocketProvider.instances = [];
  FakeWebsocketProvider.shouldThrow = false;
  getCrdtToken.mockReset();
  getCrdtToken.mockResolvedValue({ token: 'crdt-tok', expires_in: 3600 });
  useAuthStore.setState({ apiKey: 'api-key-1', tenantId: 'tenant-1', isAuthenticated: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useYjsCollab', () => {
  test('does nothing when roomId is empty', () => {
    const { result } = renderHook(() => useYjsCollab({ roomId: '' }));
    expect(result.current.text).toBe('');
    expect(result.current.connected).toBe(false);
    expect(FakeWebsocketProvider.instances.length).toBe(0);
  });

  test('creates a provider with a CRDT token when the token endpoint succeeds', async () => {
    renderHook(() => useYjsCollab({ roomId: 'room-1' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));
    const provider = latestProvider();
    expect(provider.roomName).toBe('collab-tenant-1-room-1');
    expect((provider.opts as { params: Record<string, string> }).params).toEqual({ token: 'crdt-tok' });
  });

  test('falls back to the API key when the CRDT token request fails', async () => {
    getCrdtToken.mockRejectedValue(new Error('not implemented'));
    renderHook(() => useYjsCollab({ roomId: 'room-2' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));
    const provider = latestProvider();
    expect((provider.opts as { params: Record<string, string> }).params).toEqual({ api_key: 'api-key-1' });
  });

  test('uses empty params when the token request fails and there is no API key', async () => {
    useAuthStore.setState({ apiKey: '' });
    getCrdtToken.mockRejectedValue(new Error('not implemented'));
    renderHook(() => useYjsCollab({ roomId: 'room-3' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));
    const provider = latestProvider();
    expect((provider.opts as { params: Record<string, string> }).params).toEqual({});
  });

  test('reflects connected/synced state from provider status/sync events', async () => {
    const { result } = renderHook(() => useYjsCollab({ roomId: 'room-4' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));
    const provider = latestProvider();

    act(() => provider._emit('status', { status: 'connected' }));
    await waitFor(() => expect(result.current.connected).toBe(true));

    act(() => provider._emit('sync', true));
    await waitFor(() => expect(result.current.synced).toBe(true));

    act(() => provider._emit('status', { status: 'disconnected' }));
    await waitFor(() => expect(result.current.connected).toBe(false));
  });

  test('falls back to local-only editing when the provider constructor throws', async () => {
    FakeWebsocketProvider.shouldThrow = true;
    const { result } = renderHook(() => useYjsCollab({ roomId: 'room-5' }));
    await waitFor(() => expect(result.current.connected).toBe(false));
    expect(FakeWebsocketProvider.instances.length).toBe(0);
  });

  test('setText writes to the shared doc and is reflected in `text`', async () => {
    const { result } = renderHook(() => useYjsCollab({ roomId: 'room-6' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));

    act(() => result.current.setText('hello world'));
    await waitFor(() => expect(result.current.text).toBe('hello world'));
  });

  test('setText is a no-op when the new text equals the current text', async () => {
    const { result } = renderHook(() => useYjsCollab({ roomId: 'room-7' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));

    act(() => result.current.setText('same'));
    await waitFor(() => expect(result.current.text).toBe('same'));

    // Setting the identical text again should not throw and should leave text unchanged.
    act(() => result.current.setText('same'));
    expect(result.current.text).toBe('same');
  });

  test('updateCursorPosition forwards to the provider awareness once connected', async () => {
    const { result } = renderHook(() => useYjsCollab({ roomId: 'room-8', userName: 'Ada' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));
    const provider = latestProvider();
    const spy = vi.spyOn(provider.awareness, 'setLocalStateField');

    act(() => result.current.updateCursorPosition(5, { anchor: 1, head: 5 }));
    expect(spy).toHaveBeenCalledWith('cursor', { position: 5, selection: { anchor: 1, head: 5 } });
  });

  test('remote awareness changes populate `cursors`, excluding the local client', async () => {
    const { result } = renderHook(() => useYjsCollab({ roomId: 'room-9' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));
    const provider = latestProvider();
    const localId = provider.awareness.clientID;

    act(() => {
      provider.awareness._setRemoteState(localId + 1, {
        user: { name: 'Bob', color: '#123456' },
        cursor: { position: 10, selection: { anchor: 2, head: 10 } },
      });
      // A remote peer without a user/cursor field should fall back to defaults.
      provider.awareness._setRemoteState(localId + 2, {});
    });

    await waitFor(() => expect(result.current.cursors.length).toBe(2));
    const bob = result.current.cursors.find((c) => c.clientId === localId + 1);
    expect(bob).toMatchObject({ name: 'Bob', color: '#123456', position: 10, selection: { anchor: 2, head: 10 } });
    const anon = result.current.cursors.find((c) => c.clientId === localId + 2);
    expect(anon).toMatchObject({ name: `User ${localId + 2}`, color: '#888', position: 0 });
    // Never includes the local client's own awareness state.
    expect(result.current.cursors.some((c) => c.clientId === localId)).toBe(false);
  });

  test('undo/redo update canUndo/canRedo and mutate the document', async () => {
    const { result } = renderHook(() => useYjsCollab({ roomId: 'room-10' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));

    act(() => result.current.setText('abc'));
    await waitFor(() => expect(result.current.canUndo).toBe(true));

    act(() => result.current.undo());
    await waitFor(() => expect(result.current.text).toBe(''));
    await waitFor(() => expect(result.current.canRedo).toBe(true));

    act(() => result.current.redo());
    await waitFor(() => expect(result.current.text).toBe('abc'));
  });

  test('cleans up the provider and doc on unmount', async () => {
    const { unmount } = renderHook(() => useYjsCollab({ roomId: 'room-11' }));
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));
    const provider = latestProvider();

    unmount();
    expect(provider.destroyed).toBe(true);
  });

  test('switching roomId tears down the old provider and creates a new one', async () => {
    const { rerender } = renderHook(({ roomId }: { roomId: string }) => useYjsCollab({ roomId }), {
      initialProps: { roomId: 'room-a' },
    });
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(1));
    const first = latestProvider();

    rerender({ roomId: 'room-b' });
    await waitFor(() => expect(FakeWebsocketProvider.instances.length).toBe(2));

    expect(first.destroyed).toBe(true);
    expect(latestProvider().roomName).toBe('collab-tenant-1-room-b');
  });
});
