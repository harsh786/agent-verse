import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { SettingsPage } from './SettingsPage';

// Mock clipboard API – not available in jsdom
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText: vi.fn().mockResolvedValue(undefined) },
  writable: true,
});

function renderSettingsPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SettingsPage />
    </QueryClientProvider>
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
    expect(screen.getByText('LLM Provider')).toBeInTheDocument();
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
      llmConfig: { provider: 'anthropic', model: 'claude-opus-4-5', api_key: 'sk-test' },
    });
    renderSettingsPage();
    await waitFor(() => expect(screen.getByText('anthropic')).toBeInTheDocument());
    expect(screen.getByText('claude-opus-4-5')).toBeInTheDocument();
  });

  test('shows edit form when Edit button is clicked', async () => {
    makeSettingsFetch({
      llmConfig: { provider: 'openai', model: 'gpt-4o', api_key: '' },
    });
    renderSettingsPage();
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
      if (url.endsWith('/tenants/me/llm')) {
        return new Response(
          JSON.stringify({ provider: 'openai', model: 'gpt-4o', api_key: '' }),
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

    renderSettingsPage();
    await waitFor(() => expect(screen.getByText('openai')).toBeInTheDocument());
    const editButtons = await screen.findAllByRole('button', { name: /^edit$/i });
    await userEvent.click(editButtons[0]);
    await userEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/llm$/),
        expect.objectContaining({ method: 'PUT' })
      )
    );
  });

  test('shows api key as masked when set', async () => {
    makeSettingsFetch({
      llmConfig: { provider: 'openai', model: 'gpt-4o', api_key: 'sk-secret' },
    });
    renderSettingsPage();
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
    renderSettingsPage();
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
    renderSettingsPage();
    await waitFor(() => expect(screen.getByText('production')).toBeInTheDocument());
  });

  test('shows create key input when "+ New Key" is clicked', async () => {
    makeSettingsFetch({ apiKeys: [] });
    renderSettingsPage();
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

    renderSettingsPage();
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

    renderSettingsPage();
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

    renderSettingsPage();
    await waitFor(() => expect(screen.getByText('prod-key')).toBeInTheDocument());
    // The delete button has title="Delete" and contains Trash2 icon
    await userEvent.click(screen.getByTitle('Delete'));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/tenants\/me\/keys\/key-1$/),
        expect.objectContaining({ method: 'DELETE' })
      )
    );
  });
});
