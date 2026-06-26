import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GovernancePage } from './GovernancePage';

function renderGovernancePage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <GovernancePage />
    </QueryClientProvider>
  );
}

describe('GovernancePage – Policies tab', () => {
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

  test('renders tab navigation with all four tabs', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderGovernancePage();
    expect(screen.getByText('policies')).toBeInTheDocument();
    expect(screen.getByText('approvals')).toBeInTheDocument();
    expect(screen.getByText('audit')).toBeInTheDocument();
    expect(screen.getByText('budget')).toBeInTheDocument();
  });

  test('shows empty state when no policies are defined', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    renderGovernancePage();
    await waitFor(() =>
      expect(screen.getByText(/no policies defined/i)).toBeInTheDocument()
    );
  });

  test('lists existing policies in table', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            policy_id: 'pol-1',
            name: 'block-shell',
            tools_pattern: 'shell:*',
            action: 'deny',
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    expect(screen.getByText('shell:*')).toBeInTheDocument();
    expect(screen.getByText('deny')).toBeInTheDocument();
  });

  test('shows new policy form when "New Policy" button is clicked', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    renderGovernancePage();
    await waitFor(() => expect(screen.getByText(/no policies defined/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /\+ new policy/i }));
    expect(screen.getByPlaceholderText('block-shell-commands')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('shell:*')).toBeInTheDocument();
  });

  test('creates a new policy via POST on save', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.endsWith('/governance/policies') && init?.method === 'POST') {
          return new Response(
            JSON.stringify({ policy_id: 'pol-new', name: 'no-shell', tools_pattern: 'shell:*', action: 'deny' }),
            { status: 201, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
    );

    renderGovernancePage();
    await waitFor(() => expect(screen.getByText(/no policies defined/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /\+ new policy/i }));
    await userEvent.type(screen.getByPlaceholderText('block-shell-commands'), 'no-shell');
    await userEvent.type(screen.getByPlaceholderText('shell:*'), 'shell:*');
    await userEvent.click(screen.getByRole('button', { name: /save policy/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/governance\/policies$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('deletes a policy via DELETE when Delete button is clicked', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.includes('/governance/policies/pol-1') && init?.method === 'DELETE') {
          return new Response(null, { status: 204 });
        }
        return new Response(
          JSON.stringify([{ policy_id: 'pol-1', name: 'block-shell', tools_pattern: 'shell:*', action: 'deny' }]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
    );

    renderGovernancePage();
    await waitFor(() => expect(screen.getByText('block-shell')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /delete/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/governance\/policies\/pol-1$/),
        expect.objectContaining({ method: 'DELETE' })
      )
    );
  });
});

describe('GovernancePage – Approvals tab', () => {
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

  test('shows empty state when no pending approvals', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/governance/approvals')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderGovernancePage();
    await userEvent.click(screen.getByText('approvals'));
    await waitFor(() =>
      expect(screen.getByText(/no pending approvals/i)).toBeInTheDocument()
    );
  });

  test('lists pending approval requests', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/governance/approvals')) {
        return new Response(
          JSON.stringify([
            {
              request_id: 'req-1',
              goal_id: 'goal-abc',
              tool_name: 'shell:execute',
              reason: 'Needs confirmation',
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderGovernancePage();
    await userEvent.click(screen.getByText('approvals'));
    await waitFor(() => expect(screen.getByText('shell:execute')).toBeInTheDocument());
    expect(screen.getByText('goal: goal-abc')).toBeInTheDocument();
  });

  test('approve button calls the approve API', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.includes('/approve') && init?.method === 'POST') {
          return new Response(null, { status: 204 });
        }
        if (url.endsWith('/governance/approvals')) {
          return new Response(
            JSON.stringify([{ request_id: 'req-1', goal_id: 'g1', tool_name: 'shell:exec' }]),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
    );

    renderGovernancePage();
    await userEvent.click(screen.getByText('approvals'));
    await waitFor(() => expect(screen.getByText('shell:exec')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^approve$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/governance\/approvals\/req-1\/approve$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('reject button calls the reject API', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.includes('/reject') && init?.method === 'POST') {
          return new Response(null, { status: 204 });
        }
        if (url.endsWith('/governance/approvals')) {
          return new Response(
            JSON.stringify([{ request_id: 'req-2', goal_id: 'g2', tool_name: 'github:push' }]),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
    );

    renderGovernancePage();
    await userEvent.click(screen.getByText('approvals'));
    await waitFor(() => expect(screen.getByText('github:push')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^reject$/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/governance\/approvals\/req-2\/reject$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });
});

describe('GovernancePage – Audit tab', () => {
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

  test('renders Goal ID and Tool Name search inputs', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    renderGovernancePage();
    await userEvent.click(screen.getByText('audit'));
    expect(screen.getByPlaceholderText('goal_...')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('shell:execute')).toBeInTheDocument();
  });

  test('shows audit results table after clicking Search', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/audit')) {
        return new Response(
          JSON.stringify([
            {
              entry_id: 'e1',
              goal_id: 'goal-1',
              tool_name: 'shell:run',
              action: 'execute',
              result: 'allowed',
              timestamp: new Date().toISOString(),
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderGovernancePage();
    await userEvent.click(screen.getByText('audit'));
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }));
    await waitFor(() => expect(screen.getByText('shell:run')).toBeInTheDocument());
    expect(screen.getByText('allowed')).toBeInTheDocument();
  });

  test('shows empty state when no audit entries found', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/audit')) {
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderGovernancePage();
    await userEvent.click(screen.getByText('audit'));
    await userEvent.click(screen.getByRole('button', { name: /^search$/i }));
    await waitFor(() =>
      expect(screen.getByText(/no audit entries found/i)).toBeInTheDocument()
    );
  });
});

describe('GovernancePage – Budget tab', () => {
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

  test('shows current budget limits', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/governance/budget')) {
        return new Response(
          JSON.stringify({ per_goal_usd: 1.5, per_tenant_daily_usd: 50.0 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderGovernancePage();
    await userEvent.click(screen.getByText('budget'));
    await waitFor(() => expect(screen.getByText('$1.50')).toBeInTheDocument());
    expect(screen.getByText('$50.00')).toBeInTheDocument();
  });

  test('shows edit form when Edit button is clicked', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/governance/budget')) {
        return new Response(
          JSON.stringify({ per_goal_usd: 1.0, per_tenant_daily_usd: 20.0 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderGovernancePage();
    await userEvent.click(screen.getByText('budget'));
    await waitFor(() => expect(screen.getByText('$1.00')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    expect(screen.getByRole('button', { name: /save budget/i })).toBeInTheDocument();
  });

  test('save budget calls PUT /governance/budget', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      async (input, init) => {
        const url = String(input);
        if (url.endsWith('/governance/budget') && init?.method === 'PUT') {
          return new Response(
            JSON.stringify({ per_goal_usd: 2.0, per_tenant_daily_usd: 100.0 }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        if (url.endsWith('/governance/budget')) {
          return new Response(
            JSON.stringify({ per_goal_usd: 1.0, per_tenant_daily_usd: 20.0 }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
        }
        return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
    );

    renderGovernancePage();
    await userEvent.click(screen.getByText('budget'));
    await waitFor(() => expect(screen.getByText('$1.00')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /^edit$/i }));
    await userEvent.click(screen.getByRole('button', { name: /save budget/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/governance\/budget$/),
        expect.objectContaining({ method: 'PUT' })
      )
    );
  });

  test('shows Budget Limits heading', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/governance/budget')) {
        return new Response(
          JSON.stringify({ per_goal_usd: 0.5, per_tenant_daily_usd: 10.0 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });

    renderGovernancePage();
    await userEvent.click(screen.getByText('budget'));
    await waitFor(() => expect(screen.getByText('Budget Limits')).toBeInTheDocument());
    expect(screen.getByText('Per Goal Limit')).toBeInTheDocument();
    expect(screen.getByText('Daily Tenant Limit')).toBeInTheDocument();
  });
});
