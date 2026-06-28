import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConnectorsCatalogPage } from '../ConnectorsCatalogPage';

// Module-level mock: vi.mock is hoisted by Vitest so the factory must only
// reference module-level variables, not variables declared inside tests.
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ConnectorsCatalogPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const CATALOG_ENTRIES = [
  {
    name: 'github',
    description: 'GitHub MCP connector for repository operations',
    auth_type: 'bearer',
    default_url: 'http://localhost:9001',
    category: 'dev-tools',
    connector_type: 'github',
  },
  {
    name: 'slack',
    description: 'Slack MCP connector for messaging',
    auth_type: 'api_key',
    default_url: 'http://localhost:9002',
    category: 'communication',
    connector_type: 'slack',
  },
];

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  mockNavigate.mockClear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't1', plan: 'free', isAuthenticated: true });
});

describe('ConnectorsCatalogPage', () => {
  test('renders Connector Catalog heading', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(CATALOG_ENTRIES), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    expect(await screen.findByText(/Connector Catalog/i)).toBeInTheDocument();
  });

  test('shows catalog entries when API returns data', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(CATALOG_ENTRIES), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    // Use exact match on card titles (card h3 text is lowercase as returned by the API)
    expect(await screen.findByText('github')).toBeInTheDocument();
    expect(await screen.findByText('slack')).toBeInTheDocument();
    expect(screen.getByText('GitHub MCP connector for repository operations')).toBeInTheDocument();
  });

  test('Register button is present for each catalog entry', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(CATALOG_ENTRIES), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    // Wait for card titles to appear (exact match avoids ambiguity with description text)
    await screen.findByText('github');
    const registerButtons = screen.getAllByRole('button', { name: /register/i });
    expect(registerButtons).toHaveLength(CATALOG_ENTRIES.length);
  });

  test('shows empty state when no catalog entries returned', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    expect(await screen.findByText(/No catalog entries found/i)).toBeInTheDocument();
  });

  test('Register button pre-fills navigation state with connector data', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([CATALOG_ENTRIES[0]]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    await screen.findByText('github');
    const registerButton = screen.getByRole('button', { name: /register/i });
    await userEvent.click(registerButton);
    expect(mockNavigate).toHaveBeenCalledWith('/connectors', expect.objectContaining({
      state: expect.objectContaining({
        prefill: expect.objectContaining({
          connector_type: 'github',
          name: 'github',
          url: 'http://localhost:9001',
          auth_type: 'bearer',
        }),
      }),
    }));
  });

  test('search filters catalog entries', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(CATALOG_ENTRIES), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    // Wait for both card titles to appear (exact match on lowercase names)
    await screen.findByText('github');
    await screen.findByText('slack');
    const searchInput = screen.getByRole('searchbox', { name: /search connectors/i });
    await userEvent.type(searchInput, 'slack');
    await waitFor(() => {
      expect(screen.queryByText('GitHub MCP connector for repository operations')).not.toBeInTheDocument();
      expect(screen.getByText('Slack MCP connector for messaging')).toBeInTheDocument();
    });
  });
});
