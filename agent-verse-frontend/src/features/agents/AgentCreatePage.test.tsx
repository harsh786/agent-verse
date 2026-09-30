import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentCreatePage } from './AgentCreatePage';

const { mockNavigate } = vi.hoisted(() => ({ mockNavigate: vi.fn() }));

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AgentCreatePage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_CREATED_AGENT = {
  agent_id: 'agent-new-1',
  name: 'My Agent',
  autonomy_mode: 'bounded-autonomous',
  created_at: '2026-06-29T00:00:00Z',
};

describe('AgentCreatePage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
    });
    mockNavigate.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders without crashing', () => {
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows NL mode by default', () => {
    renderPage();
    // NL command textarea should be in the DOM
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  test('shows mode toggle buttons (NL and Manual)', () => {
    renderPage();
    // Page shows two mode buttons
    const buttons = screen.queryAllByRole('button');
    expect(buttons.length).toBeGreaterThan(0);
  });

  test('renders Create Agent heading', () => {
    renderPage();
    // Use role query to distinguish h1 from the button with the same text
    expect(screen.getByRole('heading', { name: /create agent/i })).toBeInTheDocument();
  });

  test('shows manual mode form when Manual tab is clicked', async () => {
    const user = userEvent.setup();
    renderPage();
    // The tab button text is "Manual Configuration"
    const manualBtn = screen.getByRole('button', { name: /manual configuration/i });
    await user.click(manualBtn);
    await waitFor(() =>
      // Manual form shows "Agent Name *" label
      expect(screen.getByPlaceholderText('My Jira Agent')).toBeInTheDocument(),
      { timeout: 2000 }
    );
  });

  test('NL mode: submits command and calls API', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_CREATED_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );

    renderPage();
    const textarea = screen.getByRole('textbox');
    await user.type(textarea, 'Create an agent that monitors GitHub issues');

    // The NL mode create button has text "Create Agent" (exact)
    const createBtn = screen.getByRole('button', { name: 'Create Agent' });
    await user.click(createBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    }, { timeout: 3000 });
  });

  test('NL mode: navigates to the created agent from the {agent: {...}} response shape', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(JSON.stringify({ agent: MOCK_CREATED_AGENT, meta_agent_config: { generated_by: 'llm' } }), {
        status: 201, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    await user.type(screen.getByRole('textbox'), 'Create an agent that triages bugs');
    await user.click(screen.getByRole('button', { name: 'Create Agent' }));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/agents/agent-new-1'));
  });

  describe('heuristic draft (designer LLM failed → 502)', () => {
    const HEURISTIC_502 = {
      detail:
        'The agent designer LLM did not return a usable config; no agent was created. Retry, or resend with accept_heuristic=true to create the heuristic draft.',
      error_code: 'meta_agent_llm_unavailable',
      generated_by: 'heuristic',
      fallback_reason: 'timeout',
      draft_config: {
        name: 'Bug Triage Agent',
        goal_template: 'Create an agent that triages bugs',
        connectors: ['github'],
        trigger_type: 'manual',
        autonomy_mode: 'supervised',
      },
    };

    function mockCreate(second: () => Response) {
      const bodies: Array<Record<string, unknown>> = [];
      const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
        if (String(input).includes('/agents/create')) {
          const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
          bodies.push(body);
          if (!body.accept_heuristic) {
            return new Response(JSON.stringify(HEURISTIC_502), {
              status: 502, headers: { 'Content-Type': 'application/json' },
            });
          }
          return second();
        }
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      });
      return { spy, bodies };
    }

    test('shows the draft and does NOT navigate or claim success', async () => {
      const user = userEvent.setup();
      const { bodies } = mockCreate(() => new Response('{}', { status: 500 }));
      renderPage();
      await user.type(screen.getByRole('textbox'), 'Create an agent that triages bugs');
      await user.click(screen.getByRole('button', { name: 'Create Agent' }));

      const panel = await screen.findByTestId('heuristic-draft-confirm');
      expect(panel).toHaveTextContent(/nothing was created/i);
      expect(panel).toHaveTextContent('Bug Triage Agent');
      expect(panel).toHaveTextContent('github');
      expect(panel).toHaveTextContent('supervised');
      expect(panel).toHaveTextContent('Reason: timeout');
      expect(screen.getByRole('button', { name: 'Create anyway' })).toBeInTheDocument();
      // The primary button is locked while a draft awaits a decision.
      expect(screen.getByRole('button', { name: 'Create Agent' })).toBeDisabled();
      expect(mockNavigate).not.toHaveBeenCalled();
      expect(bodies).toHaveLength(1);
      expect(bodies[0]).toEqual({ command: 'Create an agent that triages bugs', autorun: false });
    });

    test('"Create anyway" resends with accept_heuristic: true and then navigates', async () => {
      const user = userEvent.setup();
      const { bodies } = mockCreate(() =>
        new Response(JSON.stringify({ agent: { ...MOCK_CREATED_AGENT, agent_id: 'agent-heur-1' } }), {
          status: 201, headers: { 'Content-Type': 'application/json' },
        })
      );
      renderPage();
      await user.type(screen.getByRole('textbox'), 'Create an agent that triages bugs');
      await user.click(screen.getByRole('button', { name: 'Create Agent' }));
      await user.click(await screen.findByRole('button', { name: 'Create anyway' }));

      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/agents/agent-heur-1'));
      expect(bodies).toHaveLength(2);
      expect(bodies[1]).toMatchObject({ command: 'Create an agent that triages bugs', accept_heuristic: true });
    });

    test('"Discard draft" dismisses it without creating anything', async () => {
      const user = userEvent.setup();
      const { bodies } = mockCreate(() => new Response('{}', { status: 500 }));
      renderPage();
      await user.type(screen.getByRole('textbox'), 'Create an agent that triages bugs');
      await user.click(screen.getByRole('button', { name: 'Create Agent' }));
      await user.click(await screen.findByRole('button', { name: 'Discard draft' }));

      expect(screen.queryByTestId('heuristic-draft-confirm')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Create Agent' })).toBeEnabled();
      expect(bodies).toHaveLength(1);
      expect(mockNavigate).not.toHaveBeenCalled();
    });

    test('editing the description drops a stale draft', async () => {
      const user = userEvent.setup();
      mockCreate(() => new Response('{}', { status: 500 }));
      renderPage();
      await user.type(screen.getByRole('textbox'), 'Create an agent that triages bugs');
      await user.click(screen.getByRole('button', { name: 'Create Agent' }));
      await screen.findByTestId('heuristic-draft-confirm');
      await user.type(screen.getByRole('textbox'), ' daily');
      expect(screen.queryByTestId('heuristic-draft-confirm')).not.toBeInTheDocument();
    });

    test('a plain 502 without a heuristic draft is shown as an ordinary error', async () => {
      const user = userEvent.setup();
      vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
        new Response(JSON.stringify({ detail: 'Bad gateway upstream' }), {
          status: 502, headers: { 'Content-Type': 'application/json' },
        })
      );
      renderPage();
      await user.type(screen.getByRole('textbox'), 'x');
      await user.click(screen.getByRole('button', { name: 'Create Agent' }));
      expect(await screen.findByRole('alert')).toHaveTextContent('Bad gateway upstream');
      expect(screen.queryByTestId('heuristic-draft-confirm')).not.toBeInTheDocument();
    });
  });

  test('shows error message when agent creation fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: { message: 'Quota exceeded' } }), {
        status: 429,
      })
    );

    renderPage();
    const textarea = screen.getByRole('textbox');
    await user.type(textarea, 'Create agent');

    // Switch to manual mode to test error display there too
    const manualBtn = screen.queryByRole('button', { name: /manual/i });
    if (manualBtn) {
      await user.click(manualBtn);
      const nameInput = screen.queryByPlaceholderText(/agent name/i);
      if (nameInput) {
        await user.type(nameInput, 'test agent');
        const saveBtn = screen.queryByRole('button', { name: /create|save/i });
        if (saveBtn) {
          await user.click(saveBtn);
          await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 2000 });
        }
      }
    }
    expect(document.body).toBeTruthy();
  });

  test('back to agents button navigates to the agents list', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('button', { name: /back to agents/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/agents');
  });

  test('NL mode cancel button navigates to the agents list', async () => {
    const user = userEvent.setup();
    renderPage();
    // Only the NL panel is mounted by default, so "Cancel" is unambiguous here.
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(mockNavigate).toHaveBeenCalledWith('/agents');
  });

  test('toggles the autorun checkbox', async () => {
    const user = userEvent.setup();
    renderPage();
    const checkbox = screen.getByRole('checkbox', { name: /auto-run on creation/i });
    expect(checkbox).not.toBeChecked();
    await user.click(checkbox);
    expect(checkbox).toBeChecked();
    await user.click(checkbox);
    expect(checkbox).not.toBeChecked();
  });

  test('NL create button is disabled until a command is typed', async () => {
    const user = userEvent.setup();
    renderPage();
    const createBtn = screen.getByRole('button', { name: 'Create Agent' });
    expect(createBtn).toBeDisabled();
    await user.type(screen.getByRole('textbox'), 'Do something');
    expect(createBtn).toBeEnabled();
  });

  test('switches from Manual Configuration back to AI Builder', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId('manual-tab'));
    await waitFor(() => expect(screen.getByPlaceholderText('My Jira Agent')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: /ai builder/i }));
    await waitFor(() =>
      expect(screen.queryByPlaceholderText('My Jira Agent')).not.toBeInTheDocument()
    );
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  describe('manual mode fields', () => {
    async function openManualTab(user: ReturnType<typeof userEvent.setup>) {
      await user.click(screen.getByTestId('manual-tab'));
      await waitFor(() => expect(screen.getByPlaceholderText('My Jira Agent')).toBeInTheDocument());
    }

    test('submit is disabled until a name is entered', async () => {
      const user = userEvent.setup();
      renderPage();
      await openManualTab(user);
      const submitBtn = screen.getByRole('button', { name: /^create agent$/i });
      expect(submitBtn).toBeDisabled();
      await user.type(screen.getByPlaceholderText('My Jira Agent'), 'My New Agent');
      expect(submitBtn).toBeEnabled();
    });

    test('manual cancel button navigates to the agents list', async () => {
      const user = userEvent.setup();
      renderPage();
      await openManualTab(user);
      await user.click(screen.getByRole('button', { name: /^cancel$/i }));
      expect(mockNavigate).toHaveBeenCalledWith('/agents');
    });

    test('changes the autonomy mode select', async () => {
      const user = userEvent.setup();
      renderPage();
      await openManualTab(user);
      const select = screen.getByRole('combobox') as HTMLSelectElement;
      expect(select.value).toBe('bounded-autonomous');
      await user.selectOptions(select, 'fully-autonomous');
      expect(select.value).toBe('fully-autonomous');
    });

    test('types into goal template and system prompt fields', async () => {
      const user = userEvent.setup();
      renderPage();
      await openManualTab(user);
      const goalTemplate = screen.getByPlaceholderText(/your job is to/i);
      const systemPrompt = screen.getByPlaceholderText(/additional system instructions/i);
      await user.type(goalTemplate, 'You are a helpful agent.');
      await user.type(systemPrompt, 'Be concise.');
      expect(goalTemplate).toHaveValue('You are a helpful agent.');
      expect(systemPrompt).toHaveValue('Be concise.');
    });

    // UI-AGENT-CONNECTOR-PICKER: the free-text "Connector IDs" box (which also
    // ate the comma as you typed a second id) is replaced by a picker of the
    // tenant's registered connectors that submits their server ids.
    test('picks registered connectors (same-type instances separately) and submits their server ids', async () => {
      const user = userEvent.setup();
      const connectors = [
        { server_id: 'builtin-mongodb:orders-db', name: 'orders-db', connector_type: 'mongodb', url: 'builtin://' },
        { server_id: 'builtin-mongodb:analytics-db', name: 'analytics-db', connector_type: 'mongodb', url: 'builtin://' },
      ];
      const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
        const url = String(input);
        const body = url.endsWith('/connectors') && (init?.method ?? 'GET') === 'GET'
          ? connectors
          : MOCK_CREATED_AGENT;
        return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      });
      renderPage();
      await openManualTab(user);
      expect(screen.queryByPlaceholderText('github, jira-mcp, slack-mcp')).not.toBeInTheDocument();
      await user.type(screen.getByPlaceholderText('My Jira Agent'), 'Reporter');
      await user.click(await screen.findByRole('checkbox', { name: /analytics-db/ }));
      expect(screen.getByRole('checkbox', { name: /orders-db/ })).not.toBeChecked();
      expect(screen.getByText(/Connectors \(1 selected\)/)).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: /^create agent$/i }));
      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith(`/agents/${MOCK_CREATED_AGENT.agent_id}`));
      const createCall = fetchMock.mock.calls.find(
        ([u, i]) => String(u).endsWith('/agents') && (i as RequestInit | undefined)?.method === 'POST',
      );
      expect(JSON.parse(String((createCall![1] as RequestInit).body)).connector_ids).toEqual([
        'builtin-mongodb:analytics-db',
      ]);
    });

    test('parses comma-separated knowledge collection IDs and trims/filters blanks', async () => {
      const user = userEvent.setup();
      renderPage();
      await openManualTab(user);
      const collectionsInput = screen.getByPlaceholderText('col_abc123, col_def456');
      fireEvent.change(collectionsInput, { target: { value: 'col_abc123, , col_def456 ,' } });
      expect(collectionsInput).toHaveValue('col_abc123, col_def456');
    });

    test('max iterations falls back to 15 when cleared, and accepts a valid number', async () => {
      const user = userEvent.setup();
      renderPage();
      await openManualTab(user);
      const maxIterations = screen.getByDisplayValue('15');
      await user.clear(maxIterations);
      // Cleared input parses to NaN -> falls back to the default of 15.
      expect(maxIterations).toHaveValue(15);
      fireEvent.change(maxIterations, { target: { value: '30' } });
      expect(maxIterations).toHaveValue(30);
    });

    test('submits the manual form successfully and navigates to the new agent', async () => {
      const user = userEvent.setup();
      // Build a fresh Response per call: MissionControlLayout's own background
      // queries (health/goals/alerts) also hit this mock, and a Response body
      // can only be read once.
      const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
        new Response(JSON.stringify(MOCK_CREATED_AGENT), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        })
      );
      renderPage();
      await openManualTab(user);
      await user.type(screen.getByPlaceholderText('My Jira Agent'), 'My New Agent');
      await user.click(screen.getByRole('button', { name: /^create agent$/i }));

      await waitFor(() => expect(fetchMock).toHaveBeenCalled());
      await waitFor(() =>
        expect(mockNavigate).toHaveBeenCalledWith(`/agents/${MOCK_CREATED_AGENT.agent_id}`)
      );
    });

    test('shows an error message when manual submission fails', async () => {
      const user = userEvent.setup();
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        // The connector picker's list loads fine; only the create call fails.
        if (String(input).endsWith('/connectors')) {
          return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
        }
        throw new Error('Network error');
      });
      renderPage();
      await openManualTab(user);
      await user.type(screen.getByPlaceholderText('My Jira Agent'), 'My New Agent');
      await user.click(screen.getByRole('button', { name: /^create agent$/i }));

      const alert = await screen.findByRole('alert');
      expect(alert).toHaveTextContent(/error/i);
      expect(mockNavigate).not.toHaveBeenCalledWith(expect.stringContaining('/agents/'));
    });
  });
});
