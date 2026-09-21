/**
 * MarketplacePage — companion branch coverage.
 *
 * Complements MarketplacePage.test.tsx (which covers grid load, quick-deploy,
 * domain filter, error/empty, and header controls) by exercising the untested
 * paths: the detail drawer, parameterised templates, the sort control, semantic
 * search, the publish modal, save-to-library, and the review flow.
 *
 * All assertions hit REAL rendered content and REAL fetch calls.
 * marketplaceApi.list returns `{ templates, ... }`; search returns `{ items, ... }`.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { MarketplacePage } from './MarketplacePage';

// A minimal IntersectionObserver stub that fires "isIntersecting: true"
// synchronously on observe(), so the infinite-scroll sentinel effect in
// MarketplacePage actually calls fetchNextPage() during tests (jsdom does
// not implement IntersectionObserver at all).
class TriggeringIntersectionObserver {
  private callback: IntersectionObserverCallback;
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
  }
  observe(target: Element) {
    this.callback(
      [{ isIntersecting: true, target } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver
    );
  }
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] { return []; }
}

// ── Factories ─────────────────────────────────────────────────────────────────

function v2Template(overrides: Record<string, unknown> = {}) {
  return {
    template_id: 'tpl-1',
    slug: 'pr-review-agent',
    name: 'PR Review Agent',
    domain: 'software',
    description: 'Automatically reviews pull requests.',
    long_description: 'A thorough long description of the PR review agent.',
    required_connectors: ['github'],
    optional_connectors: [],
    autonomy_mode: 'bounded-autonomous',
    is_builtin: true,
    is_verified: false,
    install_count: 42,
    rating_avg: 4.2,
    rating_count: 5,
    version: '1.0.0',
    visibility: 'public',
    review_status: 'approved',
    parameters_schema: {},
    template_config: { goal_template: 'Review PR {{number}}' },
    ...overrides,
  };
}

function listResponse(templates: ReturnType<typeof v2Template>[]) {
  return JSON.stringify({ templates, total: templates.length, page: 1, page_size: 50 });
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <MarketplacePage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

// Renders MarketplacePage behind a real router with a destination route, so the
// drawer's "View agent" button (which calls navigate(`/agents/:id`)) can be
// verified by asserting on where the router actually lands.
function renderPageWithAgentRoute() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={['/marketplace']}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/marketplace" element={<MarketplacePage />} />
          <Route path="/agents/:id" element={<div>AGENT DETAIL PAGE</div>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

// ── Suite ─────────────────────────────────────────────────────────────────────

describe('MarketplacePage — branches', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
  });
  afterEach(() => vi.restoreAllMocks());

  test('shows the total template count line', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(listResponse([v2Template(), v2Template({ template_id: 'tpl-2', name: 'Deploy Bot' })]))
    );
    renderPage();
    await screen.findByText('PR Review Agent');
    expect(screen.getByText(/^2 templates/)).toBeInTheDocument();
  });

  test('renders the verified badge for verified templates', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(listResponse([v2Template({ is_verified: true })]))
    );
    renderPage();
    await screen.findByText('PR Review Agent');
    expect(screen.getByLabelText('Verified')).toBeInTheDocument();
  });

  test('parameterised templates show "Configure & Deploy" instead of a quick deploy', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(
        listResponse([
          v2Template({
            parameters_schema: { properties: { repo: { type: 'string', description: 'Repository' } }, required: ['repo'] },
          }),
        ])
      )
    );
    renderPage();
    await screen.findByText('PR Review Agent');
    expect(screen.getByRole('button', { name: /Configure & Deploy/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^deploy$/i })).not.toBeInTheDocument();
  });

  test('clicking a card opens the detail drawer with long description and goal template', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));

    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('A thorough long description of the PR review agent.')).toBeInTheDocument();
    expect(within(drawer).getByText('Review PR {{number}}')).toBeInTheDocument();
    expect(within(drawer).getByText(/No reviews yet\. Be the first!/i)).toBeInTheDocument();
  });

  test('drawer renders reviews returned by the reviews endpoint', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/reviews')) {
        return jsonResponse([
          { reviewer_tenant_id: 'r1', rating: 5, title: 'Great', body: 'Loved this agent', helpful_count: 2, verified_install: true },
        ]);
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));

    expect(await screen.findByText('Loved this agent')).toBeInTheDocument();
    expect(screen.getByText('Great')).toBeInTheDocument();
    expect(screen.getByText('verified')).toBeInTheDocument();
  });

  test('drawer gates deploy on required parameters, then POSTs to the deploy endpoint', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deploy') && method === 'POST') {
        return jsonResponse({ success: true, agent_id: 'agent-77', agent_name: 'PR Review Agent' });
      }
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(
        listResponse([
          v2Template({
            parameters_schema: { properties: { repo: { type: 'string', description: 'Repository' } }, required: ['repo'] },
          }),
        ])
      );
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /Configure & Deploy/i }));

    const deployBtn = await screen.findByRole('button', { name: /Deploy Agent/i });
    expect(deployBtn).toBeDisabled();
    expect(screen.getByText(/Fill in required parameters above to deploy/i)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/repo/i), 'octo/repo');
    expect(deployBtn).toBeEnabled();

    await userEvent.click(deployBtn);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            /\/marketplace\/templates\/tpl-1\/deploy$/.test(String(u)) &&
            (i as RequestInit)?.method === 'POST'
        )
      ).toBe(true)
    );
    expect(await screen.findByText(/Agent deployed!/i)).toBeInTheDocument();
    expect(screen.getByText('agent-77')).toBeInTheDocument();
  });

  test('save-to-library button POSTs the goal pattern to /templates', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/reviews')) return jsonResponse([]);
      if (url.endsWith('/templates') && method === 'POST') {
        return jsonResponse({ id: 'gt-1', name: 'PR Review Agent' });
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));
    await userEvent.click(await screen.findByRole('button', { name: /Save to Template Library/i }));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => /\/templates$/.test(String(u)) && (i as RequestInit)?.method === 'POST'
        )
      ).toBe(true)
    );
  });

  test('review flow POSTs a rating to the reviews endpoint', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/reviews') && method === 'POST') {
        return jsonResponse({ reviewer_tenant_id: 'r', rating: 4, helpful_count: 0, verified_install: false });
      }
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));

    await userEvent.click(await screen.findByRole('button', { name: /Rate 4 stars/i }));
    await userEvent.click(screen.getByRole('button', { name: /Submit review/i }));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => /\/reviews$/.test(String(u)) && (i as RequestInit)?.method === 'POST'
        )
      ).toBe(true)
    );
  });

  test('closing the drawer removes it from the DOM', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));
    const drawer = await screen.findByRole('dialog');
    await userEvent.click(within(drawer).getByRole('button', { name: /Close drawer/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  test('sort control switches the list query to sort_by=rating', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(listResponse([v2Template()])));
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /Top Rated/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u]) => /sort_by=rating/.test(String(u)))).toBe(true)
    );
  });

  test('typing 3+ characters switches to the semantic search endpoint', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/marketplace/search')) {
        return jsonResponse({ items: [v2Template({ name: 'Semantic Hit' })], total: 1 });
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.type(screen.getByRole('textbox', { name: /search marketplace/i }), 'kube');
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => /\/marketplace\/search$/.test(String(u)) && (i as RequestInit)?.method === 'POST')).toBe(true)
    );
    expect(await screen.findByText('Semantic Hit')).toBeInTheDocument();
  });

  test('clear-search button empties the input', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(listResponse([v2Template()])));
    renderPage();
    await screen.findByText('PR Review Agent');
    const input = screen.getByRole('textbox', { name: /search marketplace/i }) as HTMLInputElement;
    await userEvent.type(input, 'ab');
    expect(input.value).toBe('ab');
    await userEvent.click(screen.getByRole('button', { name: /Clear search/i }));
    expect(input.value).toBe('');
  });

  test('publish modal submit is gated then POSTs to /marketplace/publish and shows the result', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/marketplace/publish') && method === 'POST') {
        return jsonResponse({ template_id: 'tpl-published', name: 'My Agent' });
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /^publish$/i }));

    expect(await screen.findByText(/Publish to Marketplace/i)).toBeInTheDocument();
    // Modal submit is the second "Publish" button; disabled until required fields filled.
    const submit = screen.getAllByRole('button', { name: /^publish$/i })[1];
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/^Name/i), 'My Agent');
    await userEvent.type(screen.getByLabelText(/^Description/i), 'Does things');
    await userEvent.type(screen.getByLabelText(/Goal Template/i), 'Do {{thing}}');
    expect(submit).toBeEnabled();

    await userEvent.click(submit);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => /\/marketplace\/publish$/.test(String(u)) && (i as RequestInit)?.method === 'POST')
      ).toBe(true)
    );
    expect(await screen.findByText(/Published!/i)).toBeInTheDocument();
    expect(screen.getByText('tpl-published')).toBeInTheDocument();
  });

  test('quick deploy that resolves without an agent_id shows the server-provided error via toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deploy') && method === 'POST') {
        // HTTP 200, but the deploy itself failed (e.g. missing connector auth) —
        // no agent_id, an explicit error field.
        return jsonResponse({ success: false, error: 'connector "github" is not authorized for this tenant' });
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /^deploy$/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(
        toasts.some(
          (t) => t.kind === 'error' && t.message === 'connector "github" is not authorized for this tenant'
        )
      ).toBe(true);
    });
    // No success banner should appear.
    expect(screen.queryByLabelText('Dismiss deploy banner')).not.toBeInTheDocument();
  });

  test('quick deploy that rejects with a falsy error field falls back to the default message', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deploy') && method === 'POST') {
        return jsonResponse({ success: false });
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /^deploy$/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message === 'Deploy failed')).toBe(true);
    });
  });

  test('quick deploy hitting a server error (real install failure) surfaces a "Deploy failed" toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deploy') && method === 'POST') {
        return jsonResponse({ detail: 'Deployment quota exceeded for tenant' }, 500);
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /^deploy$/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      // The underlying request() helper toasts its own "Server error: ..." message,
      // then handleQuickDeploy's catch block toasts a second, distinct message
      // prefixed with "Deploy failed:" carrying the stringified error.
      expect(
        toasts.some((t) => t.kind === 'error' && t.message.startsWith('Deploy failed:') && t.message.includes('Deployment quota exceeded'))
      ).toBe(true);
    });
    // The deploying spinner clears once the failure is handled.
    await waitFor(() => expect(screen.getByRole('button', { name: /^deploy$/i })).toBeEnabled());
  });

  test('typing 3+ characters that match nothing shows the "no results" empty state', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/marketplace/search')) {
        return jsonResponse({ items: [], total: 0 });
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.type(screen.getByRole('textbox', { name: /search marketplace/i }), 'zzzzz');

    expect(await screen.findByText(/No results for "zzzzz"/i)).toBeInTheDocument();
    expect(screen.queryByText('PR Review Agent')).not.toBeInTheDocument();
  });

  test('infinite scroll sentinel fetches the next page and shows the loading-more spinner', async () => {
    const originalIO = window.IntersectionObserver;
    Object.defineProperty(window, 'IntersectionObserver', {
      writable: true, configurable: true,
      value: TriggeringIntersectionObserver,
    });

    let resolveSecondPage!: (r: Response) => void;
    const secondPagePromise = new Promise<Response>((resolve) => { resolveSecondPage = resolve; });
    let deployCalls = 0;

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('page=2')) {
        deployCalls += 1;
        return secondPagePromise;
      }
      // First page: total=25 so hasNextPage is true (1 * PAGE_SIZE(20) < 25).
      return jsonResponse(
        JSON.stringify({ templates: [v2Template()], total: 25, page: 1, page_size: 20 })
      );
    });

    try {
      renderPage();
      await screen.findByText('PR Review Agent');

      // The sentinel's IntersectionObserver fired synchronously on mount and
      // triggered fetchNextPage() for page 2, which is still pending — the
      // "loading more" spinner should now be visible.
      await waitFor(() => expect(screen.getByLabelText('Loading more')).toBeInTheDocument());
      expect(deployCalls).toBeGreaterThan(0);

      resolveSecondPage(
        jsonResponse(JSON.stringify({ templates: [v2Template({ template_id: 'tpl-2', name: 'Page Two Template' })], total: 25, page: 2, page_size: 20 }))
      );

      await screen.findByText('Page Two Template');
      await waitFor(() => expect(screen.queryByLabelText('Loading more')).not.toBeInTheDocument());
    } finally {
      Object.defineProperty(window, 'IntersectionObserver', {
        writable: true, configurable: true,
        value: originalIO,
      });
    }
  });

  test('card shows the author byline, overflow connector count, and opens via Enter key', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      jsonResponse(
        listResponse([
          v2Template({
            author_name: 'Jane Coder',
            domain: 'some-unmapped-vertical',
            required_connectors: ['github', 'slack', 'jira', 'notion', 'linear'],
          }),
        ])
      )
    );
    renderPage();
    await screen.findByText('PR Review Agent');

    expect(screen.getByText('by Jane Coder')).toBeInTheDocument();
    expect(screen.getByText('+1 more')).toBeInTheDocument();

    const card = screen.getByRole('button', { name: /View details for PR Review Agent/i });
    // A non-Enter key must not open the drawer (false branch of the guard).
    fireEvent.keyDown(card, { key: 'a' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    // Enter opens it (true branch).
    fireEvent.keyDown(card, { key: 'Enter' });
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  test('drawer falls back to the short description, shows author/verified, and only optional connectors', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(
        listResponse([
          v2Template({
            author_name: 'Jane Coder',
            is_verified: true,
            long_description: undefined,
            required_connectors: [],
            optional_connectors: ['sentry'],
            template_config: undefined,
          }),
        ])
      );
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));

    const drawer = await screen.findByRole('dialog');
    // Falls back to the short description (no long_description).
    expect(within(drawer).getByText('Automatically reviews pull requests.')).toBeInTheDocument();
    expect(within(drawer).getByText(/by/i)).toBeInTheDocument();
    expect(within(drawer).getByText('Jane Coder')).toBeInTheDocument();
    expect(within(drawer).getByText('Verified')).toBeInTheDocument();
    // Optional connectors section renders even with no required connectors.
    expect(within(drawer).getByText('Optional')).toBeInTheDocument();
    expect(within(drawer).getByText('sentry')).toBeInTheDocument();
    expect(within(drawer).queryByText('Required')).not.toBeInTheDocument();
  });

  test('enum and URI-format parameters render a select and a url input, and the select is changeable', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deploy') && method === 'POST') {
        return jsonResponse({ success: true, agent_id: 'agent-enum-1' });
      }
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(
        listResponse([
          v2Template({
            parameters_schema: {
              properties: {
                region: { type: 'string', enum: ['us', 'eu'], description: 'Region' },
                website: { type: 'string', format: 'uri', default: 'https://example.com' },
              },
              required: ['region'],
            },
          }),
        ])
      );
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /Configure & Deploy/i }));

    const regionSelect = await screen.findByLabelText(/region/i) as HTMLSelectElement;
    const websiteInput = screen.getByLabelText(/website/i) as HTMLInputElement;
    expect(websiteInput.type).toBe('url');

    const deployBtn = screen.getByRole('button', { name: /Deploy Agent/i });
    expect(deployBtn).toBeDisabled();

    await userEvent.selectOptions(regionSelect, 'us');
    expect(regionSelect.value).toBe('us');
    expect(deployBtn).toBeEnabled();

    await userEvent.click(deployBtn);
    expect(await screen.findByText(/Agent deployed!/i)).toBeInTheDocument();
  });

  test('drawer deploy failure (server error) surfaces a "Deploy failed" toast without crashing', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deploy') && method === 'POST') {
        return jsonResponse({ detail: 'Agent quota exceeded' }, 500);
      }
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));
    const deployBtn = await screen.findByRole('button', { name: /Deploy Agent/i });
    await userEvent.click(deployBtn);

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(
        toasts.some((t) => t.kind === 'error' && t.message.startsWith('Deploy failed:') && t.message.includes('Agent quota exceeded'))
      ).toBe(true);
    });
    // No deploy-result panel should have appeared.
    expect(screen.queryByText(/Agent deployed!/i)).not.toBeInTheDocument();
  });

  test('save-to-library is disabled by a missing goal pattern and reports it via toast', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(listResponse([v2Template({ template_config: undefined })]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));
    await userEvent.click(await screen.findByRole('button', { name: /Save to Template Library/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(
        toasts.some((t) => t.kind === 'error' && t.message.includes('This template has no goal pattern to save to the library.'))
      ).toBe(true);
    });
  });

  test('review submission failure surfaces a toast, and the review body textarea is editable', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/reviews') && method === 'POST') {
        return jsonResponse({ detail: 'You must install this template before reviewing it' }, 400);
      }
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));

    const textarea = await screen.findByPlaceholderText(/Share your experience/i);
    await userEvent.type(textarea, 'Did not work for me.');
    expect(textarea).toHaveValue('Did not work for me.');

    await userEvent.click(await screen.findByRole('button', { name: /Rate 2 stars/i }));
    await userEvent.click(screen.getByRole('button', { name: /Submit review/i }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(
        toasts.some((t) => t.kind === 'error' && t.message.includes('You must install this template before reviewing it'))
      ).toBe(true);
    });
  });

  test('review with a created_at timestamp renders a formatted date', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/reviews')) {
        return jsonResponse([
          { reviewer_tenant_id: 'r1', rating: 3, body: 'Decent.', helpful_count: 0, verified_install: false, created_at: '2025-01-15T00:00:00Z' },
        ]);
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));

    expect(await screen.findByText('Decent.')).toBeInTheDocument();
    expect(screen.getByText(new Date('2025-01-15T00:00:00Z').toLocaleDateString())).toBeInTheDocument();
  });

  test('clicking "View agent" after a successful drawer deploy navigates to the agent detail route', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deploy') && method === 'POST') {
        return jsonResponse({ success: true, agent_id: 'agent-nav-1', agent_name: 'Navigated Agent' });
      }
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPageWithAgentRoute();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));
    await userEvent.click(await screen.findByRole('button', { name: /Deploy Agent/i }));

    const drawer = await screen.findByRole('dialog');
    await userEvent.click(within(drawer).getByRole('button', { name: /View agent/i }));
    expect(await screen.findByText('AGENT DETAIL PAGE')).toBeInTheDocument();
  });

  test('publish modal domain/autonomy/connectors fields are editable, and a failed publish toasts an error', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/marketplace/publish') && method === 'POST') {
        return jsonResponse({ detail: 'A template with this name already exists' }, 409);
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /^publish$/i }));
    await screen.findByText(/Publish to Marketplace/i);

    await userEvent.selectOptions(screen.getByLabelText(/^Domain/i), 'devops');
    await userEvent.selectOptions(screen.getByLabelText(/^Autonomy/i), 'fully-autonomous');
    await userEvent.type(screen.getByLabelText(/^Connectors/i), 'jira, github');
    expect((screen.getByLabelText(/^Domain/i) as HTMLSelectElement).value).toBe('devops');
    expect((screen.getByLabelText(/^Autonomy/i) as HTMLSelectElement).value).toBe('fully-autonomous');

    await userEvent.type(screen.getByLabelText(/^Name/i), 'My Agent');
    await userEvent.type(screen.getByLabelText(/^Description/i), 'Does things');
    await userEvent.type(screen.getByLabelText(/Goal Template/i), 'Do {{thing}}');
    const submit = screen.getAllByRole('button', { name: /^publish$/i })[1];
    await userEvent.click(submit);

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(
        toasts.some((t) => t.kind === 'error' && t.message.includes('Publish failed:') && t.message.includes('A template with this name already exists'))
      ).toBe(true);
    });
  });

  test('publish modal Cancel button closes it without publishing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(listResponse([v2Template()])));
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /^publish$/i }));
    await screen.findByText(/Publish to Marketplace/i);

    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByText(/Publish to Marketplace/i)).not.toBeInTheDocument();
  });

  test('dismissing the quick-deploy success banner removes it', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deploy') && method === 'POST') {
        return jsonResponse({ success: true, agent_id: 'agent-banner-1' });
      }
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /^deploy$/i }));

    expect(await screen.findByText('agent-banner-1')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Dismiss deploy banner/i }));
    expect(screen.queryByText('agent-banner-1')).not.toBeInTheDocument();
  });

  test('a template already installed (persisted in localStorage from a prior session) shows as deployed', async () => {
    useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
    localStorage.setItem('av_marketplace_installs_t', JSON.stringify(['tpl-1']));
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(listResponse([v2Template()])));
    renderPage();
    await screen.findByText('PR Review Agent');
    expect(screen.getByText('Deployed ✓')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^deploy$/i })).not.toBeInTheDocument();
  });

  test('corrupted localStorage install data is tolerated and the page still renders', async () => {
    useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
    localStorage.setItem('av_marketplace_installs_t', 'not-valid-json{{{');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(listResponse([v2Template()])));
    renderPage();
    await screen.findByText('PR Review Agent');
    // Falls back to "not installed" rather than crashing.
    expect(screen.getByRole('button', { name: /^deploy$/i })).toBeInTheDocument();
  });

  test('drawer deploy shows "Deploying…" while pending, then falls back to "Deploy failed" when no agent_id or error is returned', async () => {
    let resolveDeploy!: (r: Response) => void;
    const deployPromise = new Promise<Response>((resolve) => { resolveDeploy = resolve; });

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deploy') && method === 'POST') {
        return deployPromise;
      }
      if (url.includes('/reviews')) return jsonResponse([]);
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /View details for PR Review Agent/i }));
    await userEvent.click(await screen.findByRole('button', { name: /Deploy Agent/i }));

    // Still pending: the button reflects the in-flight state.
    expect(await screen.findByText(/Deploying…/i)).toBeInTheDocument();

    resolveDeploy(jsonResponse({ success: false }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message === 'Deploy failed')).toBe(true);
    });
    expect(screen.queryByText(/Agent deployed!/i)).not.toBeInTheDocument();
  });

  test('empty results for a selected domain mention that domain in the empty state', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('domain=devops')) return jsonResponse(listResponse([]));
      return jsonResponse(listResponse([v2Template()]));
    });
    renderPage();
    await screen.findByText('PR Review Agent');
    await userEvent.click(screen.getByRole('button', { name: /^devops$/i }));

    await waitFor(() => expect(spy.mock.calls.some(([u]) => /domain=devops/.test(String(u)))).toBe(true));
    expect(await screen.findByText(/No templates in the "devops" domain yet\./i)).toBeInTheDocument();
  });
});
