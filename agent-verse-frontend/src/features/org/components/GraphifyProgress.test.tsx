import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GraphifyProgress } from './GraphifyProgress';

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

// jsdom has no EventSource; the component opens one after a successful start.
class MockEventSource {
  url: string;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  close = vi.fn();
  constructor(url: string) { this.url = url; }
}
beforeEach(() => {
  (globalThis as unknown as { EventSource: unknown }).EventSource = MockEventSource;
});

function mockFetch(startStatus = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/graphify') && method === 'POST') {
      if (startStatus !== 200) return new Response('nope', { status: startStatus });
      return new Response(JSON.stringify({ job_id: 'job-1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/tenants/stream-token'))
      return new Response(JSON.stringify({ token: 'stream-tok' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderProgress(props: Partial<React.ComponentProps<typeof GraphifyProgress>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <GraphifyProgress orgId="o1" {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('GraphifyProgress', () => {
  test('renders the idle state with zeroed stat tiles and a build button', () => {
    mockFetch();
    renderProgress();
    expect(screen.getByText('Knowledge Graph')).toBeInTheDocument();
    expect(screen.getByText(/Graphify — AI-powered org analysis/i)).toBeInTheDocument();
    expect(screen.getByText('Nodes')).toBeInTheDocument();
    expect(screen.getByText('Insights')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Start building knowledge graph/i })).toBeInTheDocument();
  });

  test('clicking build POSTs to the graphify endpoint and leaves the idle state', async () => {
    const spy = mockFetch();
    renderProgress();
    fireEvent.click(screen.getByRole('button', { name: /Start building knowledge graph/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/v1\/org\/o1\/graphify$/.test(String(u)) && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
    // The build button is gone once the run is queued/running.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Start building knowledge graph/i })).not.toBeInTheDocument(),
    );
  });

  test('a failed start shows the error state and a retry button', async () => {
    mockFetch(500);
    renderProgress();
    fireEvent.click(screen.getByRole('button', { name: /Start building knowledge graph/i }));
    expect(await screen.findByText(/Failed to start: 500/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Retry knowledge graph build/i })).toBeInTheDocument();
  });

  test('the close button invokes onClose', () => {
    mockFetch();
    const onClose = vi.fn();
    renderProgress({ onClose });
    fireEvent.click(screen.getByRole('button', { name: /Close graphify panel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
