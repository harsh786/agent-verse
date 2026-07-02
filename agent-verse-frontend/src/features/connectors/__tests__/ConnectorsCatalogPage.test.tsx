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

const RICH_CATALOG_ENTRIES = [
  {
    name: 'jira',
    display_name: 'Jira',
    description: 'JIRA — project management, issue tracking',
    auth_type: 'basic',
    default_url: 'https://your-domain.atlassian.net',
    icon: 'jira',
    category: 'project_management',
    auth_fields: [
      { key: 'url', label: 'Jira URL', placeholder: 'https://co.atlassian.net', field_type: 'url', required: true, hint: 'Your Atlassian instance URL' },
      { key: 'username', label: 'Email', placeholder: 'you@co.com', field_type: 'email', required: true, hint: '' },
      { key: 'password', label: 'API Token', placeholder: 'ATATT3x...', field_type: 'password', required: true, hint: 'Create at id.atlassian.com' },
    ],
    has_builtin: true,
    builtin_server_id: 'builtin-jira',
    is_configured: false,
    connector_type: 'jira',
  },
  {
    name: 'github',
    display_name: 'GitHub',
    description: 'GitHub — code repositories, PRs, issues',
    auth_type: 'bearer',
    default_url: 'https://api.github.com',
    icon: 'github',
    category: 'devtools',
    auth_fields: [
      { key: 'token', label: 'Personal Access Token', placeholder: 'ghp_xxx', field_type: 'password', required: true, hint: '' },
    ],
    has_builtin: true,
    builtin_server_id: 'builtin-github',
    is_configured: true,
    connector_type: 'github',
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
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    expect(await screen.findByText(/Connector Catalog/i)).toBeInTheDocument();
  });

  test('shows catalog entries when API returns data', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    expect(await screen.findByText('Jira')).toBeInTheDocument();
    expect(await screen.findByText('GitHub')).toBeInTheDocument();
    expect(screen.getByText('JIRA — project management, issue tracking')).toBeInTheDocument();
  });

  test('Configure button is present for each catalog entry', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    await screen.findByText('Jira');
    const configureButtons = screen.getAllByRole('button', { name: /configure/i });
    expect(configureButtons).toHaveLength(RICH_CATALOG_ENTRIES.length);
  });

  test('shows empty state when no catalog entries returned', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    expect(await screen.findByText(/No connectors match your search/i)).toBeInTheDocument();
  });

  test('Configure button pre-fills navigation state with connector data', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([RICH_CATALOG_ENTRIES[0]]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    await screen.findByText('Jira');
    const configureButton = screen.getByRole('button', { name: /configure/i });
    await userEvent.click(configureButton);
    expect(mockNavigate).toHaveBeenCalledWith('/connectors', expect.objectContaining({
      state: expect.objectContaining({
        prefill: expect.objectContaining({
          connector_type: 'jira',
          name: 'jira',
          url: 'https://your-domain.atlassian.net',
          auth_type: 'basic',
        }),
      }),
    }));
  });

  test('search filters catalog entries', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    await screen.findByText('Jira');
    await screen.findByText('GitHub');
    const searchInput = screen.getByRole('searchbox', { name: /search connectors/i });
    await userEvent.type(searchInput, 'github');
    await waitFor(() => {
      expect(screen.queryByText('JIRA — project management, issue tracking')).not.toBeInTheDocument();
      expect(screen.getByText('GitHub — code repositories, PRs, issues')).toBeInTheDocument();
    });
  });

  test('shows Native badge for connectors with has_builtin', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    renderPage();
    await screen.findByText('Jira');
    const nativeBadges = screen.getAllByText(/native/i);
    expect(nativeBadges.length).toBeGreaterThan(0);
  });

  test('shows Configured badge for is_configured connectors', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    renderPage();
    await screen.findByText('GitHub');
    const configuredEls = screen.getAllByText(/configured/i);
    expect(configuredEls.length).toBeGreaterThan(0);
  });

  test('shows auth field hints in the card', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    renderPage();
    await screen.findByText('Jira');
    expect(screen.getByText(/Your Atlassian instance URL/)).toBeInTheDocument();
  });

  test('category filter buttons are rendered', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    renderPage();
    await screen.findByText('Jira');
    expect(screen.getByRole('button', { name: /All/i })).toBeInTheDocument();
  });
});
