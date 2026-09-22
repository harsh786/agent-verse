import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
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

  test('renders no close button when onClose is not provided', async () => {
    mockFetch();
    renderMarket();
    await screen.findByText('GitHub');
    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument();
  });

  test('shows a loading skeleton while the catalog is in flight', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(() => {}));
    renderMarket();
    expect(screen.getByLabelText('Loading connectors')).toBeInTheDocument();
    expect(screen.getByText('Loading…')).toBeInTheDocument();
  });

  test('switching category filters the grid to that category', async () => {
    mockFetch();
    renderMarket();
    await screen.findByText('GitHub');
    fireEvent.click(screen.getByRole('tab', { name: 'communication' }));
    expect(screen.queryByText('GitHub')).not.toBeInTheDocument();
    expect(screen.getByText('Slack')).toBeInTheDocument();
  });

  test('a non-OAuth connect opens the credential form', async () => {
    const catalog = [
      { name: 'notion', display_name: 'Notion', description: 'Notes', auth_type: 'api_key', default_url: '', icon: '', category: 'productivity', auth_fields: [], has_builtin: false, builtin_server_id: null, is_configured: false, connector_type: 'mcp' },
    ];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(catalog), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url))
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('Notion');
    fireEvent.click(screen.getByRole('button', { name: 'Connect Notion' }));
    expect(await screen.findByRole('dialog', { name: 'Connect Notion' })).toBeInTheDocument();
  });

  test('an OAuth connect opens a popup on success', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(CATALOG), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url) && method === 'GET')
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/connectors/oauth/start'))
        return new Response(JSON.stringify({ auth_url: 'https://slack.test/oauth/authorize', state: 's1' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('Slack');
    fireEvent.click(screen.getByRole('button', { name: 'Connect Slack' }));
    await waitFor(() => expect(openSpy).toHaveBeenCalled());
    expect(openSpy.mock.calls[0][0]).toBe('https://slack.test/oauth/authorize');
    openSpy.mockRestore();
  });

  test('an OAuth connect falls back to the credential form when the server has no OAuth app configured', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(CATALOG), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url) && method === 'GET')
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/connectors/oauth/start'))
        return new Response('nope', { status: 400 });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('Slack');
    fireEvent.click(screen.getByRole('button', { name: 'Connect Slack' }));
    // Falls through to the credential form modal for Slack.
    expect(await screen.findByRole('dialog', { name: 'Connect Slack' })).toBeInTheDocument();
  });

  test('a failed test shows a failure status for the connector', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (/\/connectors\/[^/]+\/test$/.test(url) && method === 'POST')
        return new Response(JSON.stringify({ detail: 'Bad credentials' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(CATALOG), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url))
        return new Response(JSON.stringify(INSTALLED), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('GitHub');
    fireEvent.click(screen.getByRole('button', { name: /Test GitHub/i }));
    expect(await screen.findByText('Bad credentials')).toBeInTheDocument();
  });

  test('a test that throws a non-Error value falls back to a generic "Test failed" message', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (/\/connectors\/[^/]+\/test$/.test(url) && method === 'POST') throw 'boom';
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(CATALOG), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url))
        return new Response(JSON.stringify(INSTALLED), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('GitHub');
    fireEvent.click(screen.getByRole('button', { name: /Test GitHub/i }));
    expect(await screen.findByText('Test failed')).toBeInTheDocument();
  });

  test('falls back to a generic "Verified." message when a passed test result carries no detail', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (/\/connectors\/[^/]+\/test$/.test(url) && method === 'POST')
        return new Response(JSON.stringify({ status: 'passed' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(CATALOG), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url))
        return new Response(JSON.stringify(INSTALLED), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('GitHub');
    fireEvent.click(screen.getByRole('button', { name: /Test GitHub/i }));
    expect(await screen.findByText('Verified.')).toBeInTheDocument();
  });

  test('falls back to a generic "Verification failed." message when a failed test result carries no error', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (/\/connectors\/[^/]+\/test$/.test(url) && method === 'POST')
        return new Response(JSON.stringify({ status: 'failed' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(CATALOG), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url))
        return new Response(JSON.stringify(INSTALLED), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('GitHub');
    fireEvent.click(screen.getByRole('button', { name: /Test GitHub/i }));
    expect(await screen.findByText('Verification failed.')).toBeInTheDocument();
  });

  test('shows the raw auth_type label when it is not in the known AUTH_LABEL map', async () => {
    const catalog = [
      { name: 'custom', display_name: 'CustomSys', description: 'A custom connector', auth_type: 'saml', default_url: '', icon: '', category: 'dev_tools', auth_fields: [], has_builtin: false, builtin_server_id: null, is_configured: false, connector_type: 'mcp' },
    ];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(catalog), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url))
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('CustomSys');
    expect(screen.getByText('saml')).toBeInTheDocument();
  });

  test('shows a plain "Connected" badge (no test/remove) for a built-in connector with no installed record', async () => {
    const catalog = [
      { name: 'openai', display_name: 'OpenAI', description: 'Built-in', auth_type: 'none', default_url: '', icon: '', category: 'dev_tools', auth_fields: [], has_builtin: true, builtin_server_id: 'builtin-openai', is_configured: true, connector_type: 'mcp' },
    ];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(catalog), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url))
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('OpenAI');
    expect(screen.getByText('Connected')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Test OpenAI/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Remove OpenAI/i })).not.toBeInTheDocument();
  });

  test('clicking inside the credential form modal does not close it, but clicking the backdrop does', async () => {
    const catalog = [
      { name: 'notion', display_name: 'Notion', description: 'Notes', auth_type: 'api_key', default_url: '', icon: '', category: 'productivity', auth_fields: [], has_builtin: false, builtin_server_id: null, is_configured: false, connector_type: 'mcp' },
    ];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(catalog), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url))
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('Notion');
    fireEvent.click(screen.getByRole('button', { name: 'Connect Notion' }));
    const dialog = await screen.findByRole('dialog', { name: 'Connect Notion' });

    // Clicking inside the modal's content wrapper is swallowed (stopPropagation) —
    // the modal stays open.
    fireEvent.click(dialog);
    expect(screen.getByRole('dialog', { name: 'Connect Notion' })).toBeInTheDocument();

    // Clicking the backdrop itself closes it.
    fireEvent.click(dialog.parentElement!.parentElement!);
    expect(screen.queryByRole('dialog', { name: 'Connect Notion' })).not.toBeInTheDocument();
  });

  test('the credential form\'s own Close button also dismisses the modal', async () => {
    const catalog = [
      { name: 'notion', display_name: 'Notion', description: 'Notes', auth_type: 'api_key', default_url: '', icon: '', category: 'productivity', auth_fields: [], has_builtin: false, builtin_server_id: null, is_configured: false, connector_type: 'mcp' },
    ];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors/catalog'))
        return new Response(JSON.stringify(catalog), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (/\/connectors$/.test(url))
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderMarket();
    await screen.findByText('Notion');
    fireEvent.click(screen.getByRole('button', { name: 'Connect Notion' }));
    await screen.findByRole('dialog', { name: 'Connect Notion' });
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Connect Notion' })).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog', { name: 'Connect Notion' })).not.toBeInTheDocument();
  });
});
