import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
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

// Stub out OAuthPopupButton so we can trigger its onSuccess callback directly
// without driving the real popup/postMessage OAuth flow.
vi.mock('../OAuthPopupButton', () => ({
  OAuthPopupButton: ({
    connectorName,
    onSuccess,
  }: {
    connectorName: string;
    onSuccess?: () => void;
  }) => (
    <button type="button" onClick={() => onSuccess?.()}>
      oauth-connect-{connectorName}
    </button>
  ),
}));

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ConnectorsCatalogPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { qc, ...utils };
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

const OAUTH_ENTRY = {
  name: 'slack',
  display_name: 'Slack',
  description: 'Slack — team messaging and notifications',
  auth_type: 'oauth_ac',
  default_url: 'https://slack.com',
  icon: 'slack',
  category: 'communication',
  auth_fields: [],
  has_builtin: false,
  builtin_server_id: null,
  is_configured: false,
  connector_type: 'slack',
};

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

  test('shows error state when the catalog request fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network down'));
    renderPage();
    expect(
      await screen.findByText(/Failed to load catalog\. Make sure the backend is running\./i),
    ).toBeInTheDocument();
  });

  test('toggles the built-in explanation callout', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    renderPage();
    await screen.findByText('Jira');

    expect(screen.queryByText(/Built-in connectors run inside AgentVerse/i)).not.toBeInTheDocument();

    const infoToggle = screen.getByRole('button', { name: /What is Built-in\?/i });
    await userEvent.click(infoToggle);
    expect(screen.getByText(/Built-in connectors run inside AgentVerse/i)).toBeInTheDocument();

    await userEvent.click(infoToggle);
    expect(screen.queryByText(/Built-in connectors run inside AgentVerse/i)).not.toBeInTheDocument();
  });

  test('built-in only filter hides non-builtin connectors', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([...RICH_CATALOG_ENTRIES, OAUTH_ENTRY]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    await screen.findByText('Jira');
    expect(screen.getByText('Slack')).toBeInTheDocument();

    const builtinToggle = screen.getByRole('button', { name: /Built-in only/i });
    await userEvent.click(builtinToggle);
    expect(builtinToggle).toHaveAttribute('aria-pressed', 'true');

    await waitFor(() => {
      expect(screen.queryByText('Slack')).not.toBeInTheDocument();
    });
    expect(screen.getByText('Jira')).toBeInTheDocument();
    expect(screen.getByText('GitHub')).toBeInTheDocument();
  });

  test('category button filters entries down to that category', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([...RICH_CATALOG_ENTRIES, OAUTH_ENTRY]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    renderPage();
    await screen.findByText('Jira');

    const categoryGroup = screen.getByRole('group', { name: /Filter by category/i });
    const devToolsButton = within(categoryGroup).getByRole('button', { name: /Dev Tools/i });
    await userEvent.click(devToolsButton);
    expect(devToolsButton).toHaveAttribute('aria-pressed', 'true');

    await waitFor(() => {
      expect(screen.queryByText('Jira')).not.toBeInTheDocument();
      expect(screen.queryByText('Slack')).not.toBeInTheDocument();
    });
    expect(screen.getByText('GitHub')).toBeInTheDocument();
  });

  test('"My Connectors" button navigates to /connectors', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(RICH_CATALOG_ENTRIES), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    );
    renderPage();
    await screen.findByText('Jira');
    await userEvent.click(screen.getByRole('button', { name: /My Connectors/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/connectors');
  });

  test('renders an OAuth connect button for oauth_ac connectors and invalidates queries on success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([OAUTH_ENTRY]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const { qc } = renderPage();
    await screen.findByText('Slack');

    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');
    const oauthButton = screen.getByRole('button', { name: /oauth-connect-slack/i });
    await userEvent.click(oauthButton);

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['connectors-catalog'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['connectors'] });
  });
});
