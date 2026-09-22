import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
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
  constructor(url: string) { this.url = url; mockEventSourceInstances.push(this); }
}
let mockEventSourceInstances: MockEventSource[] = [];
beforeEach(() => {
  mockEventSourceInstances = [];
  (globalThis as unknown as { EventSource: unknown }).EventSource = MockEventSource;
});

function send(es: MockEventSource, data: Record<string, unknown>) {
  act(() => { es.onmessage?.({ data: JSON.stringify(data) } as MessageEvent); });
}

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

  test('renders no close button when onClose is not provided', () => {
    mockFetch();
    renderProgress();
    expect(screen.queryByRole('button', { name: /Close graphify panel/i })).not.toBeInTheDocument();
  });

  describe('live SSE stream', () => {
    async function startAndConnect() {
      mockFetch();
      renderProgress();
      fireEvent.click(screen.getByRole('button', { name: /Start building knowledge graph/i }));
      await waitFor(() => expect(mockEventSourceInstances.length).toBe(1));
      return mockEventSourceInstances[0];
    }

    test('a "phase" event advances the phase label and progress percentage', async () => {
      const es = await startAndConnect();
      send(es, { type: 'phase', phase: 2, total_phases: 4, label: 'Connecting relationships' });
      expect(await screen.findByText('Building Graph')).toBeInTheDocument();
      expect(screen.getByText('Connecting relationships')).toBeInTheDocument();
      expect(screen.getByText('50%')).toBeInTheDocument();
    });

    test('a "stats" event updates the stat tiles incrementally', async () => {
      const es = await startAndConnect();
      send(es, { type: 'stats', nodes: 42, edges: 10 });
      expect(await screen.findByText('42')).toBeInTheDocument();
      expect(screen.getByText('10')).toBeInTheDocument();
      // communities/discoveries were untouched by this frame — stay at 0.
      expect(screen.getAllByText('0').length).toBeGreaterThan(0);
    });

    test('a "complete" event finishes the run, fires onComplete and shows the summary', async () => {
      const onComplete = vi.fn();
      mockFetch();
      renderProgress({ onComplete });
      fireEvent.click(screen.getByRole('button', { name: /Start building knowledge graph/i }));
      await waitFor(() => expect(mockEventSourceInstances.length).toBe(1));
      const es = mockEventSourceInstances[0];
      send(es, { type: 'complete', nodes: 5, edges: 3, communities: 1, discoveries: 2 });
      expect(await screen.findByText(/Graph built — 5 nodes, 3 edges/)).toBeInTheDocument();
      expect(onComplete).toHaveBeenCalledWith({ nodes: 5, edges: 3, communities: 1, discoveries: 2 });
      expect(es.close).toHaveBeenCalled();
    });

    test('the "View glowing graph" button appears on complete and invokes onViewGraph', async () => {
      const onViewGraph = vi.fn();
      mockFetch();
      renderProgress({ onViewGraph });
      fireEvent.click(screen.getByRole('button', { name: /Start building knowledge graph/i }));
      await waitFor(() => expect(mockEventSourceInstances.length).toBe(1));
      send(mockEventSourceInstances[0], { type: 'complete', nodes: 1, edges: 1 });
      const btn = await screen.findByRole('button', { name: /View glowing knowledge graph/i });
      fireEvent.click(btn);
      expect(onViewGraph).toHaveBeenCalledTimes(1);
    });

    test('an "error" event surfaces the message and closes the stream', async () => {
      const es = await startAndConnect();
      send(es, { type: 'error', message: 'Extraction crashed' });
      expect(await screen.findByText('Extraction crashed')).toBeInTheDocument();
      expect(es.close).toHaveBeenCalled();
    });

    test('an error event with no message falls back to a generic message', async () => {
      const es = await startAndConnect();
      send(es, { type: 'error' });
      expect(await screen.findByText('Unknown error')).toBeInTheDocument();
    });

    test('a "connected" event is a no-op', async () => {
      const es = await startAndConnect();
      send(es, { type: 'connected' });
      // Still in the running (queued) phase — no crash, no phase change.
      expect(screen.getByText('Queued')).toBeInTheDocument();
    });

    test('a legacy bare phase string updates phase and progress directly', async () => {
      const es = await startAndConnect();
      send(es, { phase: 'discovery', progress: 77 });
      expect(await screen.findByText('Discovering')).toBeInTheDocument();
      expect(screen.getByText('77%')).toBeInTheDocument();
    });

    test('a malformed SSE frame is ignored without crashing', async () => {
      const es = await startAndConnect();
      act(() => { es.onmessage?.({ data: 'not json' } as MessageEvent); });
      // Still shows the queued phase — nothing blew up.
      expect(screen.getByText('Queued')).toBeInTheDocument();
    });

    test('onerror on the EventSource surfaces a disconnected error', async () => {
      const es = await startAndConnect();
      act(() => { es.onerror?.(new Event('error')); });
      expect(await screen.findByText('Stream disconnected')).toBeInTheDocument();
      expect(es.close).toHaveBeenCalled();
    });

    test('connects with an empty stream token when the token fetch fails', async () => {
      const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
        const url = String(input);
        const method = (init?.method ?? 'GET').toUpperCase();
        if (url.includes('/graphify') && method === 'POST')
          return new Response(JSON.stringify({ job_id: 'job-1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        if (url.includes('/tenants/stream-token')) return new Response('nope', { status: 500 });
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      });
      renderProgress();
      fireEvent.click(screen.getByRole('button', { name: /Start building knowledge graph/i }));
      await waitFor(() => expect(mockEventSourceInstances.length).toBe(1));
      expect(mockEventSourceInstances[0].url).toContain('token=');
      spy.mockRestore();
    });
  });
});
