import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CreateMissionDrawer } from './CreateMissionDrawer';

// AnimatePresence gates the drawer; stub it so exit animations don't linger.
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const stubCache = new Map<string, (props: { children?: ReactNode; [k: string]: unknown }) => React.ReactElement>();
  const makeStub = (tag: string) => {
    let stub = stubCache.get(tag);
    if (!stub) {
      stub = ({ children, ...props }) => React.createElement(tag, props as Record<string, unknown>, children);
      stubCache.set(tag, stub);
    }
    return stub;
  };
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/missions/execute') && method === 'POST')
      return new Response(JSON.stringify({ mission_id: 'm-new', title: 'Research trends', status: 'active', goal_id: 'g1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderDrawer(props: Partial<React.ComponentProps<typeof CreateMissionDrawer>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CreateMissionDrawer orgId="o1" open={props.open ?? true} onClose={props.onClose ?? vi.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CreateMissionDrawer', () => {
  test('renders nothing when closed', () => {
    mockFetch();
    renderDrawer({ open: false });
    expect(screen.queryByRole('heading', { name: /New Mission/i })).not.toBeInTheDocument();
  });

  test('renders the form fields when open', () => {
    mockFetch();
    renderDrawer({ open: true });
    expect(screen.getByRole('heading', { name: /New Mission/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Mission title/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Objective/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Priority/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Autonomy level/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Create mission/i })).toBeInTheDocument();
  });

  test('submitting empty shows a validation error and does not POST', async () => {
    const spy = mockFetch();
    renderDrawer({ open: true });
    fireEvent.click(screen.getByRole('button', { name: /Create mission/i }));
    expect(await screen.findByText(/Title is required/i)).toBeInTheDocument();
    expect(spy.mock.calls.some(([, i]) => (i as RequestInit)?.method === 'POST')).toBe(false);
  });

  test('a valid submit POSTs to /missions/execute and closes the drawer', async () => {
    const spy = mockFetch();
    const onClose = vi.fn();
    renderDrawer({ open: true, onClose });
    fireEvent.change(screen.getByLabelText(/Mission title/i), { target: { value: 'Research trends' } });
    fireEvent.click(screen.getByRole('button', { name: /Create mission/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          if (!String(u).includes('/missions/execute') || (i as RequestInit)?.method !== 'POST') return false;
          return JSON.parse(String((i as RequestInit).body)).title === 'Research trends';
        }),
      ).toBe(true),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  test('the close button invokes onClose', () => {
    mockFetch();
    const onClose = vi.fn();
    renderDrawer({ open: true, onClose });
    fireEvent.click(screen.getByRole('button', { name: /Close drawer/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
