import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConnectorMarketplace } from './ConnectorMarketplace';

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

const CATALOG = [
  { name: 'github', display_name: 'GitHub', description: 'Open PRs and read repos', auth_type: 'api_key', default_url: '', icon: '', category: 'dev_tools', auth_fields: [], has_builtin: true, builtin_server_id: null, is_configured: false, connector_type: 'mcp' },
  { name: 'slack', display_name: 'Slack', description: 'Post messages to channels', auth_type: 'oauth_ac', default_url: '', icon: '', category: 'communication', auth_fields: [], has_builtin: false, builtin_server_id: null, is_configured: false, connector_type: 'mcp' },
];
const INSTALLED = [
  { server_id: 'srv-gh', name: 'github', url: '', auth_type: 'api_key', auth_config: {}, has_builtin: true },
];

function mockFetch(opts: { catalogStatus?: number } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (/\/connectors\/[^/]+\/test$/.test(url) && method === 'POST')
      return new Response(JSON.stringify({ server_id: 'srv-gh', status: 'passed', detail: 'Verified', latency_ms: 12 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (/\/connectors\/[^/]+$/.test(url) && method === 'DELETE')
      return new Response(null, { status: 204 });
    if (url.includes('/connectors/catalog')) {
      if (opts.catalogStatus && opts.catalogStatus !== 200) return new Response('nope', { status: opts.catalogStatus });
      return new Response(JSON.stringify(CATALOG), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (/\/connectors$/.test(url))
      return new Response(JSON.stringify(INSTALLED), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderMarket(props: Partial<React.ComponentProps<typeof ConnectorMarketplace>> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ConnectorMarketplace orgId="o1" {...props} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ConnectorMarketplace', () => {
  test('renders the catalog with a connected count', async () => {
    mockFetch();
    renderMarket();
    expect(screen.getByRole('heading', { name: /Connector Marketplace/i })).toBeInTheDocument();
    expect(await screen.findByText('GitHub')).toBeInTheDocument();
    expect(screen.getByText('Slack')).toBeInTheDocument();
    // github is installed → 1 connected of 2 available.
    expect(await screen.findByText(/1 connected · 2 available/i)).toBeInTheDocument();
  });

  test('search filters the grid and shows an empty message on no match', async () => {
    mockFetch();
    renderMarket();
    await screen.findByText('GitHub');
    fireEvent.change(screen.getByLabelText(/Search connectors/i), { target: { value: 'zzzznope' } });
    expect(await screen.findByText(/No connectors match/i)).toBeInTheDocument();
  });

  test('an installed connector can be tested — POSTs to /test', async () => {
    const spy = mockFetch();
    renderMarket();
    await screen.findByText('GitHub');
    fireEvent.click(screen.getByRole('button', { name: /Test GitHub/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/connectors\/srv-gh\/test$/.test(String(u)) && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
    expect(await screen.findByText('Verified')).toBeInTheDocument();
  });

  test('removing an installed connector DELETEs it', async () => {
    const spy = mockFetch();
    renderMarket();
    await screen.findByText('GitHub');
    fireEvent.click(screen.getByRole('button', { name: /Remove GitHub/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/connectors\/srv-gh$/.test(String(u)) && (i as RequestInit)?.method === 'DELETE'),
      ).toBe(true),
    );
  });

  test('shows an error state when the catalog fails to load', async () => {
    mockFetch({ catalogStatus: 500 });
    renderMarket();
    expect(await screen.findByText(/load the connector catalog/i)).toBeInTheDocument();
  });

  test('the close button invokes onClose', async () => {
    mockFetch();
    const onClose = vi.fn();
    renderMarket({ onClose });
    await screen.findByText('GitHub');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
