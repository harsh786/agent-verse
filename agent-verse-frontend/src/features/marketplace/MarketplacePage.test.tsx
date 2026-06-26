import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MarketplacePage } from './MarketplacePage';

function renderMarketplacePage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MarketplacePage />
    </QueryClientProvider>
  );
}

describe('MarketplacePage', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders domain filter buttons', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderMarketplacePage();
    expect(screen.getByRole('button', { name: /^all$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^software$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^devops$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^testing$/i })).toBeInTheDocument();
  });

  test('shows loading state while fetching templates', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderMarketplacePage();
    expect(screen.getByText(/loading marketplace/i)).toBeInTheDocument();
  });

  test('loads and displays templates from API', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            template_id: 'tpl-1',
            name: 'PR Review Agent',
            domain: 'software',
            description: 'Automatically reviews pull requests.',
            connectors: ['github'],
          },
          {
            template_id: 'tpl-2',
            name: 'Deploy Bot',
            domain: 'devops',
            description: 'Handles deployment workflows.',
            connectors: ['kubernetes'],
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review Agent')).toBeInTheDocument());
    expect(screen.getByText('Automatically reviews pull requests.')).toBeInTheDocument();
    expect(screen.getByText('Deploy Bot')).toBeInTheDocument();
  });

  test('Deploy button triggers mutation and calls deploy API', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.includes('/deploy') && init?.method === 'POST') {
          return new Response(
            JSON.stringify({ agent_id: 'agent-deployed-1', name: 'PR Review Agent' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(
          JSON.stringify([
            {
              template_id: 'tpl-1',
              name: 'PR Review Agent',
              domain: 'software',
              description: 'Reviews PRs.',
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
    );

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review Agent')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^deploy$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/marketplace\/tpl-1\/deploy$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('shows deployed agent ID on successful deploy', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/deploy') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ agent_id: 'agt-xyz', name: 'PR Review Agent' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(
        JSON.stringify([
          {
            template_id: 'tpl-1',
            name: 'PR Review Agent',
            domain: 'software',
            description: 'Reviews PRs.',
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    });

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review Agent')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^deploy$/i }));
    await waitFor(() => expect(screen.getByText(/agt-xyz/)).toBeInTheDocument());
  });

  test('filters templates by domain when filter button is clicked', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          { template_id: 'tpl-1', name: 'PR Review', domain: 'software', description: 'Software agent.' },
          { template_id: 'tpl-2', name: 'K8s Deploy', domain: 'devops', description: 'DevOps agent.' },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review')).toBeInTheDocument());
    expect(screen.getByText('K8s Deploy')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^software$/i }));
    expect(screen.getByText('PR Review')).toBeInTheDocument();
    expect(screen.queryByText('K8s Deploy')).not.toBeInTheDocument();
  });

  test('shows error state when API fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 500, statusText: 'Internal Server Error' })
    );

    renderMarketplacePage();
    await waitFor(() =>
      expect(screen.getByText(/failed to load marketplace/i)).toBeInTheDocument()
    );
  });

  test('shows empty state when no templates match domain filter', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          { template_id: 'tpl-1', name: 'PR Review', domain: 'software', description: 'Software agent.' },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderMarketplacePage();
    await waitFor(() => expect(screen.getByText('PR Review')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^hr$/i }));
    expect(screen.getByText(/no templates found/i)).toBeInTheDocument();
  });
});
