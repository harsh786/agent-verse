/**
 * MarketplacePage tests — updated for V2 API.
 *
 * V2 API contract (mocked here):
 *   GET  /marketplace/templates?... → { templates: [...], total, page, page_size }
 *   POST /marketplace/templates/:id/deploy → { success, agent_id, agent_name, ... }
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MarketplacePage } from './MarketplacePage';

// ── V2 template factory ──────────────────────────────────────────────────────

function v2Template(overrides: Partial<{
  template_id: string; name: string; domain: string; description: string;
  required_connectors: string[]; install_count: number; autonomy_mode: string;
  is_verified: boolean; parameters_schema: Record<string, unknown>;
  rating_avg: number; rating_count: number;
}> = {}) {
  return {
    template_id: 'tpl-1',
    slug: 'pr-review-agent',
    name: 'PR Review Agent',
    domain: 'software',
    description: 'Automatically reviews pull requests.',
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
    ...overrides,
  };
}

function v2ListResponse(templates: ReturnType<typeof v2Template>[]) {
  return JSON.stringify({ templates, total: templates.length, page: 1, page_size: 50 });
}

function renderMarketplacePage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <MarketplacePage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe('MarketplacePage', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
    sessionStorage.setItem('av_api_key', 'tenant-key');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders domain filter buttons including new V2 domains', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderMarketplacePage();
    expect(screen.getByRole('button', { name: /^all$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^software$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^devops$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^testing$/i })).toBeInTheDocument();
    // V2-only domains
    expect(screen.getByRole('button', { name: /^legal$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^finance$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^healthcare$/i })).toBeInTheDocument();
  });

  test('shows skeleton loading state while fetching', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderMarketplacePage();
    // Loading shows skeleton divs, not named template cards
    expect(screen.queryByText('PR Review Agent')).not.toBeInTheDocument();
  });

  test('loads and displays V2 templates', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        v2ListResponse([
          v2Template({ template_id: 'tpl-1', name: 'PR Review Agent', domain: 'software' }),
          v2Template({ template_id: 'tpl-2', name: 'Deploy Bot', domain: 'devops',
                       description: 'Handles deployment workflows.' }),
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review Agent')).toBeInTheDocument());
    expect(screen.getByText('Automatically reviews pull requests.')).toBeInTheDocument();
    expect(screen.getByText('Deploy Bot')).toBeInTheDocument();
    expect(screen.getByText('Handles deployment workflows.')).toBeInTheDocument();
  });

  test('shows install count and rating from V2 data', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        v2ListResponse([
          v2Template({ install_count: 123, rating_avg: 4.5, rating_count: 10 }),
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review Agent')).toBeInTheDocument());
    expect(screen.getByText(/123 installs/i)).toBeInTheDocument();
  });

  test('Deploy button calls V2 deploy endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.includes('/deploy') && init?.method === 'POST') {
          return new Response(
            JSON.stringify({ success: true, agent_id: 'agent-deployed-1', agent_name: 'PR Review Agent' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(
          v2ListResponse([v2Template()]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
    );

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review Agent')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^deploy$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        // V2 path: /marketplace/templates/{id}/deploy
        expect.stringMatching(/\/marketplace\/templates\/tpl-1\/deploy$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('shows deployed agent ID after successful deploy', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/deploy') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ success: true, agent_id: 'agt-xyz', agent_name: 'PR Review Agent' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(
        v2ListResponse([v2Template()]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review Agent')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^deploy$/i }));
    await waitFor(() => expect(screen.getByText(/agt-xyz/)).toBeInTheDocument());
  });

  test('filters templates by domain via server-side query', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      // When software domain is requested, return only the software template
      if (url.includes('domain=software')) {
        return new Response(
          v2ListResponse([v2Template({ template_id: 'tpl-1', name: 'PR Review', domain: 'software' })]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      // Default: return both
      return new Response(
        v2ListResponse([
          v2Template({ template_id: 'tpl-1', name: 'PR Review', domain: 'software' }),
          v2Template({ template_id: 'tpl-2', name: 'K8s Deploy', domain: 'devops' }),
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review')).toBeInTheDocument());
    expect(screen.getByText('K8s Deploy')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^software$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/domain=software/),
        expect.anything()
      )
    );
    // After domain filter the software-only response is shown
    await waitFor(() => expect(screen.queryByText('K8s Deploy')).not.toBeInTheDocument());
  });

  test('shows error state when API fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 500, statusText: 'Internal Server Error' })
    );

    renderMarketplacePage();
    await waitFor(() =>
      // V2 error message
      expect(screen.getByText(/could not load marketplace/i)).toBeInTheDocument()
    );
  });

  test('shows empty state when API returns no templates', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        v2ListResponse([]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderMarketplacePage();
    await waitFor(() =>
      expect(screen.getByText(/no templates found/i)).toBeInTheDocument()
    );
  });

  test('renders Publish button', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderMarketplacePage();
    expect(screen.getByRole('button', { name: /^publish$/i })).toBeInTheDocument();
  });

  test('renders search input', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderMarketplacePage();
    expect(screen.getByRole('textbox', { name: /search marketplace/i })).toBeInTheDocument();
  });
});
