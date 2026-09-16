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
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MarketplacePage } from './MarketplacePage';

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
});
