import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useThemeStore } from '@/stores/theme';
import { SettingsPage } from './SettingsPage';

// Mock clipboard API – not available in jsdom
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText: vi.fn().mockResolvedValue(undefined) },
  writable: true,
});

function renderSettingsPage(tab = 'profile') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={[`/settings?tab=${tab}`]}>
      <QueryClientProvider client={queryClient}>
        <SettingsPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

/** Default fetch mock that satisfies all three initial queries */
function makeSettingsFetch({
  tenant,
  llmConfig,
  apiKeys,
}: {
  tenant?: object;
  llmConfig?: object;
  apiKeys?: object[];
}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith('/tenants/me/llm') && (!init?.method || init.method === 'GET')) {
      return new Response(
        JSON.stringify(llmConfig ?? { provider: 'openai', model: 'gpt-4o', api_key: '' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.endsWith('/tenants/me/keys') && (!init?.method || init.method === 'GET')) {
      return new Response(
        JSON.stringify(apiKeys ?? []),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.endsWith('/tenants/me') && (!init?.method || init.method === 'GET')) {
      return new Response(
        JSON.stringify(tenant ?? { tenant_id: 'tid-1', name: 'Test Corp', email: 'test@corp.com', plan: 'pro' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    return new Response(null, { status: 404 });
  });
}

describe('SettingsPage – Profile section', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'pro',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders Settings page title', () => {
    makeSettingsFetch({});
    renderSettingsPage();
    expect(screen.getByText('Settings')).toBeInTheDocument();
  });

  test('shows tenant profile data from API', async () => {
    makeSettingsFetch({
      tenant: { tenant_id: 'tid-abc', name: 'ACME Corp', email: 'admin@acme.com', plan: 'enterprise' },
    });
    renderSettingsPage();
    await waitFor(() => expect(screen.getByText('ACME Corp')).toBeInTheDocument());
    expect(screen.getByText('admin@acme.com')).toBeInTheDocument();
    expect(screen.getByText('enterprise')).toBeInTheDocument();
  });

  test('shows Profile, LLM Provider and API Keys section headings', async () => {
    makeSettingsFetch({});
    renderSettingsPage();
    await waitFor(() => expect(screen.getByText('Profile')).toBeInTheDocument());
    expect(screen.getByText('LLM Providers')).toBeInTheDocument();
    expect(screen.getByText('API Keys')).toBeInTheDocument();
  });
});

describe('SettingsPage – LLM Provider section', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'pro',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('displays current LLM provider and model', async () => {
    makeSettingsFetch({
      llmConfig: { provider: 'anthropic', default_model: 'claude-opus-4-5', masked_key: '****' },
    });
    renderSettingsPage('llm');
    await waitFor(() => expect(screen.getByText('anthropic')).toBeInTheDocument());
    expect(screen.getByText('claude-opus-4-5')).toBeInTheDocument();
  });

  test('shows edit form when Edit button is clicked', async () => {
    makeSettingsFetch({
      llmConfig: { provider: 'openai', model: 'gpt-4o', api_key: '' },
    });
    renderSettingsPage('llm');
    await waitFor(() => expect(screen.getByText('openai')).toBeInTheDocument());
    // Click Edit for LLM Provider (first Edit button on the page)
    const editButtons = await screen.findAllByRole('button', { name: /^edit$/i });
    await userEvent.click(editButtons[0]);
    expect(screen.getByRole('button', { name: /^save$/i })).toBeInTheDocument();
  });

  test('Save button calls PUT /tenants/me/llm', async () => {
    const fetchMock = makeSettingsFetch({
      llmConfig: { provider: 'openai', model: 'gpt-4o', api_key: '' },
    });
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/tenants/me/llm') && init?.method === 'PUT') {
        return new Response(
          JSON.stringify({ provider: 'openai', model: 'gpt-4o-mini' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.endsWith('/tenants/me/llm-config') && init?.method === 'PUT') {
        return new Response(
          JSON.stringify({ provider: 'openai', default_model: 'gpt-4o-mini' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.endsWith('/tenants/me/llm')) {
        return new Response(
          JSON.stringify({ provider: 'openai', model: 'gpt-4o', api_key: '' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.endsWith('/tenants/me/llm-config')) {
        return new Response(
          JSON.stringify({ provider: 'openai', default_model: 'gpt-4o' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.endsWith('/tenants/me/keys')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/tenants/me')) {
        return new Response(
          JSON.stringify({ tenant_id: 'tid-1', name: 'Corp', plan: 'pro' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(null, { status: 404 });
    });

    renderSettingsPage('llm');
    await waitFor(() => expect(screen.getByText('openai')).toBeInTheDocument());
    const editButtons = await screen.findAllByRole('button', { name: /^edit$/i });
    await userEvent.click(editButtons[0]);

    // Switching the provider updates the suggested default model (covers the
    // provider <select>'s onChange handler).
    const providerSelect = screen.getByDisplayValue('openai') as HTMLSelectElement;
    const modelLabel = screen.getByText('Model');
    const modelInput = modelLabel.nextElementSibling as HTMLInputElement;
    await userEvent.selectOptions(providerSelect, 'anthropic');
    expect(modelInput.value).toBe('claude-opus-4-5');
    await userEvent.selectOptions(providerSelect, 'openai');

    await userEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/llm$/),
        expect.objectContaining({ method: 'PUT' })
      )
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/llm-config$/),
        expect.objectContaining({ method: 'PUT' })
      )
    );
    // onSuccess closes the edit form back to the read-only view.
    await waitFor(() => expect(screen.queryByRole('button', { name: /^save$/i })).not.toBeInTheDocument());
  });

  test('shows api key as masked when set', async () => {
    makeSettingsFetch({
      llmConfig: { provider: 'openai', default_model: 'gpt-4o', masked_key: '****' },
    });
    renderSettingsPage('llm');
    await waitFor(() => expect(screen.getByText('••••••••')).toBeInTheDocument());
  });
});

describe('SettingsPage – API Keys section', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'pro',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('shows empty API keys state message', async () => {
    makeSettingsFetch({ apiKeys: [] });
    renderSettingsPage('apikeys');
    await waitFor(() =>
      expect(screen.getByText(/no api keys/i)).toBeInTheDocument()
    );
  });

  test('lists existing API keys', async () => {
    makeSettingsFetch({
      apiKeys: [
        {
          key_id: 'key-1',
          name: 'production',
          created_at: '2026-01-01T00:00:00Z',
          last_used_at: '2026-06-01T00:00:00Z',
        },
      ],
    });
    renderSettingsPage('apikeys');
    await waitFor(() => expect(screen.getByText('production')).toBeInTheDocument());
  });

  test('shows create key input when "+ New Key" is clicked', async () => {
    makeSettingsFetch({ apiKeys: [] });
    renderSettingsPage('apikeys');
    await waitFor(() => expect(screen.getByText(/no api keys/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /\+ new key/i }));
    expect(screen.getByPlaceholderText(/key name/i)).toBeInTheDocument();
  });

  test('calls POST /tenants/me/keys when Create button is clicked', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.endsWith('/tenants/me/keys') && init?.method === 'POST') {
          return new Response(
            JSON.stringify({
              key_id: 'key-new',
              name: 'staging',
              created_at: new Date().toISOString(),
              raw_key: 'av_staging_supersecret123',
            }),
            { status: 201, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (url.endsWith('/tenants/me/keys')) {
          return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (url.endsWith('/tenants/me/llm')) {
          return new Response(
            JSON.stringify({ provider: 'openai', model: 'gpt-4o' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (url.endsWith('/tenants/me')) {
          return new Response(
            JSON.stringify({ tenant_id: 'tid-1', name: 'Corp', plan: 'pro' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(null, { status: 404 });
      }
    );

    renderSettingsPage('apikeys');
    await waitFor(() => expect(screen.getByText(/no api keys/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /\+ new key/i }));
    await userEvent.type(screen.getByPlaceholderText(/key name/i), 'staging');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/keys$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('shows newly created key banner with raw key', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/tenants/me/keys') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            key_id: 'key-new',
            name: 'test-key',
            created_at: new Date().toISOString(),
            raw_key: 'av_test_raw_key_value',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.endsWith('/tenants/me/keys')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/tenants/me/llm')) {
        return new Response(JSON.stringify({ provider: 'openai', model: 'gpt-4o' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/tenants/me')) {
        return new Response(JSON.stringify({ tenant_id: 'tid-1', name: 'Corp', plan: 'pro' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(null, { status: 404 });
    });

    renderSettingsPage('apikeys');
    await waitFor(() => expect(screen.getByText(/no api keys/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /\+ new key/i }));
    await userEvent.type(screen.getByPlaceholderText(/key name/i), 'test-key');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));

    await waitFor(() =>
      expect(screen.getByText('av_test_raw_key_value')).toBeInTheDocument()
    );
    expect(screen.getByText(/key created/i)).toBeInTheDocument();
  });

  test('calls DELETE when revoke key button is clicked', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.includes('/tenants/me/keys/key-1') && init?.method === 'DELETE') {
          return new Response(null, { status: 204 });
        }
        if (url.endsWith('/tenants/me/keys')) {
          return new Response(
            JSON.stringify([
              { key_id: 'key-1', name: 'prod-key', created_at: '2026-01-01T00:00:00Z' },
            ]),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (url.endsWith('/tenants/me/llm')) {
          return new Response(JSON.stringify({ provider: 'openai', model: 'gpt-4o' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        if (url.endsWith('/tenants/me')) {
          return new Response(JSON.stringify({ tenant_id: 'tid-1', name: 'Corp', plan: 'pro' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        return new Response(null, { status: 404 });
      }
    );

    renderSettingsPage('apikeys');
    await waitFor(() => expect(screen.getByText('prod-key')).toBeInTheDocument());
    // Open the destructive confirmation, then confirm the deletion.
    await userEvent.click(screen.getByTitle('Delete'));
    await userEvent.click(await screen.findByRole('button', { name: /^delete key$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/keys\/key-1$/),
        expect.objectContaining({ method: 'DELETE' })
      )
    );
  });

  test('shows an error message when API keys fail to load', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/tenants/me/keys')) {
        return new Response(null, { status: 500 });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderSettingsPage('apikeys');
    expect(await screen.findByText(/failed to load api keys/i)).toBeInTheDocument();
  });

  test('copies the newly created raw key to the clipboard and dismisses the banner', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/tenants/me/keys') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            key_id: 'key-new',
            name: 'copy-key',
            created_at: new Date().toISOString(),
            raw_key: 'av_copy_me_raw',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.endsWith('/tenants/me/keys')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderSettingsPage('apikeys');
    await waitFor(() => expect(screen.getByText(/no api keys/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /\+ new key/i }));
    await userEvent.type(screen.getByPlaceholderText(/key name/i), 'copy-key');
    await userEvent.click(screen.getByRole('button', { name: /^create$/i }));

    await screen.findByText('av_copy_me_raw');
    const copyButton = screen.getByText('av_copy_me_raw').parentElement!.querySelector('button')!;
    await userEvent.click(copyButton);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('av_copy_me_raw');

    await userEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(screen.queryByText('av_copy_me_raw')).not.toBeInTheDocument();
  });

  test('rotates an API key when the rotate button is clicked', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/tenants/me/keys/key-1/rotate') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ key_id: 'key-1', name: 'prod-key', created_at: new Date().toISOString(), raw_key: 'av_rotated' }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.endsWith('/tenants/me/keys')) {
        return new Response(
          JSON.stringify([{ key_id: 'key-1', name: 'prod-key', created_at: '2026-01-01T00:00:00Z' }]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderSettingsPage('apikeys');
    await waitFor(() => expect(screen.getByText('prod-key')).toBeInTheDocument());
    await userEvent.click(screen.getByTitle('Rotate'));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/keys\/key-1\/rotate$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
    expect(await screen.findByText('av_rotated')).toBeInTheDocument();
  });
});

describe('SettingsPage – sidebar navigation', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'pro',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders all settings section links, including Billing', () => {
    makeSettingsFetch({});
    renderSettingsPage();
    expect(screen.getByRole('button', { name: /general/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /llm providers/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /api keys/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /security/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /notifications/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /appearance/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /danger zone/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /billing/i })).toHaveAttribute('href', '/settings/billing');
  });

  test('clicking a sidebar tab switches the rendered section', async () => {
    makeSettingsFetch({});
    renderSettingsPage();
    await waitFor(() => expect(screen.getByText('Profile')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /appearance/i }));
    expect(screen.getByText('Information Density')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /notifications/i }));
    expect(screen.getByText('Notification Preferences')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /danger zone/i }));
    expect(screen.getByText('Export All Data')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /^security$/i }));
    expect(screen.getByText('Active Sessions')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /general/i }));
    await waitFor(() => expect(screen.getByText('Profile')).toBeInTheDocument());
  });

  test('renders no section content for an unrecognized tab value', () => {
    makeSettingsFetch({});
    renderSettingsPage('not-a-real-tab');
    expect(screen.getByText('Settings')).toBeInTheDocument();
    expect(screen.queryByText('Profile')).not.toBeInTheDocument();
    expect(screen.queryByText('Theme')).not.toBeInTheDocument();
  });

  test('renders default profile tab when no tab query param is set', () => {
    makeSettingsFetch({});
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter initialEntries={['/settings']}>
        <QueryClientProvider client={queryClient}>
          <SettingsPage />
        </QueryClientProvider>
      </MemoryRouter>
    );
    expect(screen.getByText('Settings')).toBeInTheDocument();
  });
});

describe('SettingsPage – Security tab', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'pro',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mockSecurityFetch({
    sessions,
    mfaEnabled = false,
  }: {
    sessions?: object[];
    mfaEnabled?: boolean;
  } = {}) {
    return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/auth/mfa/status')) {
        return new Response(
          JSON.stringify({ enabled: mfaEnabled, has_pending_enrollment: false, recovery_codes_count: 5 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.endsWith('/tenants/me/sessions') && method === 'GET') {
        return new Response(JSON.stringify(sessions ?? []), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (/\/tenants\/me\/sessions\/.+/.test(url) && method === 'DELETE') {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith('/tenants/me/llm')) {
        return new Response(JSON.stringify({ provider: 'openai', model: 'gpt-4o' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.endsWith('/tenants/me/keys')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/tenants/me')) {
        return new Response(JSON.stringify({ tenant_id: 'tid-1', name: 'Corp', plan: 'pro' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });
  }

  test('shows the current session and MFA management', async () => {
    mockSecurityFetch({ mfaEnabled: false });
    renderSettingsPage('security');
    expect(await screen.findByText('Current session')).toBeInTheDocument();
    expect(screen.getByText('This device')).toBeInTheDocument();
    expect(await screen.findByText('MFA Disabled')).toBeInTheDocument();
    expect(screen.getByText(/manage scopes/i)).toBeInTheDocument();
  });

  test('lists other active sessions and revokes one', async () => {
    const fetchMock = mockSecurityFetch({
      sessions: [{ session_id: 'sess-2', device: 'iPhone 15', last_seen: '2 hours ago' }],
    });
    renderSettingsPage('security');
    expect(await screen.findByText('iPhone 15')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /revoke/i }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/sessions\/sess-2$/),
        expect.objectContaining({ method: 'DELETE' })
      )
    );
  });
});

describe('SettingsPage – Notifications tab', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'pro',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders all notification preference toggles with default state', async () => {
    makeSettingsFetch({});
    renderSettingsPage('notifications');
    expect(await screen.findByText('Notification Preferences')).toBeInTheDocument();
    expect(screen.getByText('Goal completed')).toBeInTheDocument();
    expect(screen.getByText('Weekly digest')).toBeInTheDocument();
    const switches = screen.getAllByRole('switch');
    expect(switches).toHaveLength(5);
    // weeklyReport defaults to false, the rest default to true
    expect(switches[4]).toHaveAttribute('aria-checked', 'false');
    expect(switches[0]).toHaveAttribute('aria-checked', 'true');
  });

  test('toggling a preference flips it, persists to localStorage, and PUTs to the API', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/tenants/me/notifications') && init?.method === 'PUT') {
        return new Response(null, { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderSettingsPage('notifications');
    await screen.findByText('Notification Preferences');
    await userEvent.click(screen.getByText('Weekly digest'));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/notifications$/),
        expect.objectContaining({ method: 'PUT' })
      )
    );
    const stored = JSON.parse(localStorage.getItem('av_notification_prefs')!);
    expect(stored.weeklyReport).toBe(true);
  });

  test('falls back gracefully when the notifications PUT endpoint is unavailable', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/tenants/me/notifications') && init?.method === 'PUT') {
        return new Response(null, { status: 404 });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    renderSettingsPage('notifications');
    await screen.findByText('Notification Preferences');
    await userEvent.click(screen.getByText('Goal failed'));
    const stored = JSON.parse(localStorage.getItem('av_notification_prefs')!);
    expect(stored.goalFailed).toBe(false);
  });

  test('falls back to defaults when localStorage contains corrupted JSON', async () => {
    localStorage.setItem('av_notification_prefs', '{not valid json');
    makeSettingsFetch({});
    renderSettingsPage('notifications');
    await screen.findByText('Notification Preferences');
    const switches = screen.getAllByRole('switch');
    // Defaults: everything true except weeklyReport
    expect(switches[0]).toHaveAttribute('aria-checked', 'true');
    expect(switches[4]).toHaveAttribute('aria-checked', 'false');
  });

  test('loads persisted preferences from localStorage on mount', async () => {
    localStorage.setItem(
      'av_notification_prefs',
      JSON.stringify({
        goalComplete: false,
        goalFailed: false,
        budgetAlert: false,
        hitlPending: false,
        weeklyReport: true,
      })
    );
    makeSettingsFetch({});
    renderSettingsPage('notifications');
    await screen.findByText('Notification Preferences');
    const switches = screen.getAllByRole('switch');
    expect(switches[0]).toHaveAttribute('aria-checked', 'false');
    expect(switches[4]).toHaveAttribute('aria-checked', 'true');
  });
});

describe('SettingsPage – Appearance tab', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'pro',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders theme and density options', async () => {
    makeSettingsFetch({});
    renderSettingsPage('appearance');
    expect(await screen.findByText('Theme')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^light$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^dark$/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^system$/i })).toBeInTheDocument();
    expect(screen.getByText('Compact')).toBeInTheDocument();
    expect(screen.getByText('Comfortable')).toBeInTheDocument();
  });

  test('selecting a theme option updates the active selection', async () => {
    makeSettingsFetch({});
    renderSettingsPage('appearance');
    await screen.findByText('Theme');
    await userEvent.click(screen.getByRole('button', { name: /^light$/i }));
    expect(useThemeStore.getState().theme).toBe('light');
  });

  test('selecting a density option updates the active selection', async () => {
    makeSettingsFetch({});
    renderSettingsPage('appearance');
    await screen.findByText('Information Density');
    await userEvent.click(screen.getByText('Comfortable'));
    expect(useThemeStore.getState().density).toBe('comfortable');
  });
});

describe('SettingsPage – Danger Zone tab', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'pro',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders export and delete account sections', async () => {
    makeSettingsFetch({});
    renderSettingsPage('danger');
    expect(await screen.findByText('Export All Data')).toBeInTheDocument();
    expect(screen.getAllByText('Delete Account').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /export data/i })).toBeInTheDocument();
  });

  test('clicking Export Data calls the export endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/tenants/me/export') && init?.method === 'POST') {
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    // jsdom doesn't implement anchor .click() download / URL.createObjectURL by default
    URL.createObjectURL = vi.fn().mockReturnValue('blob:mock');
    URL.revokeObjectURL = vi.fn();

    renderSettingsPage('danger');
    await screen.findByText('Export All Data');
    await userEvent.click(screen.getByRole('button', { name: /export data/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/export$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('shows a spinner and disables the button while exporting', async () => {
    let resolveFn: (v: Response) => void = () => {};
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/tenants/me/export') && init?.method === 'POST') {
        return new Promise<Response>((resolve) => { resolveFn = resolve; });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    URL.createObjectURL = vi.fn().mockReturnValue('blob:mock');
    URL.revokeObjectURL = vi.fn();

    renderSettingsPage('danger');
    await screen.findByText('Export All Data');
    await userEvent.click(screen.getByRole('button', { name: /export data/i }));

    expect(await screen.findByRole('button', { name: /exporting/i })).toBeDisabled();

    resolveFn(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    await waitFor(() => expect(screen.getByRole('button', { name: /export data/i })).not.toBeDisabled());
  });

  test('reveals a DELETE confirmation input when Delete Account is clicked, and gates the confirm button', async () => {
    makeSettingsFetch({});
    renderSettingsPage('danger');
    await screen.findByRole('heading', { name: 'Delete Account' });
    await userEvent.click(screen.getByRole('button', { name: /^delete account$/i }));

    expect(screen.getByText(/are you absolutely sure/i)).toBeInTheDocument();
    const confirmInput = screen.getByPlaceholderText('Type DELETE');
    const confirmButton = screen.getByRole('button', { name: /^confirm$/i });
    expect(confirmButton).toBeDisabled();

    await userEvent.type(confirmInput, 'not delete');
    expect(confirmButton).toBeDisabled();

    await userEvent.clear(confirmInput);
    await userEvent.type(confirmInput, 'DELETE');
    expect(confirmButton).not.toBeDisabled();
  });

  test('cancelling the delete confirmation hides the input again', async () => {
    makeSettingsFetch({});
    renderSettingsPage('danger');
    await screen.findByRole('heading', { name: 'Delete Account' });
    await userEvent.click(screen.getByRole('button', { name: /^delete account$/i }));
    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(screen.queryByPlaceholderText('Type DELETE')).not.toBeInTheDocument();
  });

  test('confirming DELETE calls the delete-account endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith('/tenants/me') && init?.method === 'DELETE') {
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderSettingsPage('danger');
    await screen.findByRole('heading', { name: 'Delete Account' });
    await userEvent.click(screen.getByRole('button', { name: /^delete account$/i }));
    await userEvent.type(screen.getByPlaceholderText('Type DELETE'), 'DELETE');
    await userEvent.click(screen.getByRole('button', { name: /^confirm$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me$/),
        expect.objectContaining({ method: 'DELETE' })
      )
    );
  });
});
