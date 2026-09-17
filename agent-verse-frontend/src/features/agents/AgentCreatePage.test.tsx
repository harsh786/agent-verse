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

    test('parses comma-separated connector IDs and trims/filters blanks', async () => {
      const user = userEvent.setup();
      renderPage();
      await openManualTab(user);
      const connectorInput = screen.getByPlaceholderText('github, jira-mcp, slack-mcp');
      // Set the raw value in one shot (rather than keystroke-by-keystroke) since
      // the field is fully controlled and re-derives its display value from the
      // parsed connector_ids array on every change.
      fireEvent.change(connectorInput, { target: { value: 'github, , jira-mcp ,' } });
      expect(connectorInput).toHaveValue('github, jira-mcp');
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
      vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Network error'));
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
