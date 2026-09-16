import { render, screen, act, waitFor } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

import { CursorPresence } from './CursorPresence';

// ── Controllable WebSocket mock ────────────────────────────────────────────────
let sockets: MockWebSocket[] = [];
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  url: string;
  protocols?: string | string[];
  readyState = MockWebSocket.OPEN;
  onmessage: ((e: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    sockets.push(this);
  }
  send() {}
  close() { this.readyState = MockWebSocket.CLOSED; }
}

function emit(msg: Record<string, unknown>) {
  act(() => { sockets[0]?.onmessage?.({ data: JSON.stringify(msg) }); });
}

beforeEach(() => {
  sockets = [];
  sessionStorage.clear(); localStorage.clear();
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = MockWebSocket;
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CursorPresence', () => {
  test('renders nothing when there is no API key to authenticate with', () => {
    useAuthStore.setState({ apiKey: '', isAuthenticated: false });
    const { container } = render(<CursorPresence orgId="org-1" />);
    expect(container.firstChild).toBeNull();
    expect(sockets).toHaveLength(0);
  });

  test('opens a presence WebSocket but renders nothing until a peer appears', async () => {
    const { container } = render(<CursorPresence orgId="org-1" />);
    await waitFor(() => expect(sockets.length).toBeGreaterThan(0));
    expect(sockets[0].url).toContain('/collab/presence/org-1/ws');
    expect(container.firstChild).toBeNull();
  });

  test('shows an avatar and count when a peer joins', async () => {
    render(<CursorPresence orgId="org-1" />);
    await waitFor(() => expect(sockets.length).toBeGreaterThan(0));
    emit({ type: 'presence.update', userId: 'user-1', name: 'Alice' });
    expect(await screen.findByText('Al')).toBeInTheDocument();
    expect(screen.getByText('1 viewing')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveAttribute('aria-label', '1 person viewing');
  });

  test('tracks multiple peers and removes one on leave', async () => {
    render(<CursorPresence orgId="org-1" />);
    await waitFor(() => expect(sockets.length).toBeGreaterThan(0));
    emit({ type: 'presence.update', userId: 'user-1', name: 'Alice' });
    emit({ type: 'presence.update', userId: 'user-2', name: 'Bob' });
    expect(await screen.findByText('2 viewing')).toBeInTheDocument();
    emit({ type: 'presence.leave', userId: 'user-2' });
    await waitFor(() => expect(screen.getByText('1 viewing')).toBeInTheDocument());
  });

  test('collapses overflow into a +N badge beyond five peers', async () => {
    render(<CursorPresence orgId="org-1" />);
    await waitFor(() => expect(sockets.length).toBeGreaterThan(0));
    for (let i = 0; i < 6; i++) {
      emit({ type: 'presence.update', userId: `user-${i}`, name: `Name${i}` });
    }
    expect(await screen.findByText('6 viewing')).toBeInTheDocument();
    // 5 avatars visible, the 6th folded into a "+1" overflow badge.
    expect(screen.getByText('+1')).toBeInTheDocument();
    expect(screen.getByLabelText('1 more people')).toBeInTheDocument();
  });
});
