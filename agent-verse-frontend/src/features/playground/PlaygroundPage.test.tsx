import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { PlaygroundPage } from './PlaygroundPage';
import { useAuthStore } from '@/stores/auth';

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock('@/stores/toast', () => ({
  toast: vi.fn(),
}));

const mockFetch = vi.fn();

beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  mockFetch.mockResolvedValue({
    ok: true,
    json: async () => ({ tools: [], total: 0 }),
    body: null,
    status: 200,
  });
  vi.stubGlobal('fetch', mockFetch);
  vi.stubGlobal('crypto', { randomUUID: () => 'test-uuid-1234' });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

function renderPage() {
  return render(<PlaygroundPage />, { wrapper });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('PlaygroundPage', () => {
  it('renders scenario library section', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Saved Scenarios')).toBeDefined();
    });
  });

  it('loads available tools on mount via simulationApi.getAvailableTools', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        tools: [
          { name: 'jira:search', description: 'Search Jira', server_id: 'jira' },
          { name: 'github:list_pulls', description: 'List PRs', server_id: 'github' },
        ],
        total: 2,
      }),
      body: null,
      status: 200,
    });

    renderPage();

    await waitFor(() => {
      const elements = screen.getAllByText(/jira:search/i);
      expect(elements.length).toBeGreaterThan(0);
    });
  });

  it('quick template "Jira Search" populates goal and tools', async () => {
    renderPage();

    const jiraButton = await waitFor(() => screen.getByText('Jira Search'));
    fireEvent.click(jiraButton);

    await waitFor(() => {
      const textarea = screen.getByPlaceholderText(/describe what the agent/i);
      expect((textarea as HTMLTextAreaElement).value).toContain('Jira');
    });
  });

  it('run simulation button fires fetch to /enterprise/simulation/stream', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('simulation/stream')) {
        return { ok: false, body: null, status: 500, json: async () => ({}) };
      }
      if (String(url).includes('simulation') && !String(url).includes('available')) {
        return {
          ok: true,
          json: async () => ({
            status: 'completed',
            steps: [],
            cost_usd: 0.001,
            iterations: 0,
          }),
          body: null,
          status: 201,
        };
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();

    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Test goal for simulation' } });

    const runButton = screen.getByText('Run Simulation');
    fireEvent.click(runButton);

    await waitFor(() => {
      const calls = mockFetch.mock.calls.map((c: unknown[]) => String(c[0]));
      expect(calls.some((url) => url.includes('simulation'))).toBe(true);
    });
  });

  it('steps appear in execution canvas during SSE streaming', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"type":"step_started","step_number":1,"description":"Analyse goal requirements"}\n\n'
          )
        );
        controller.enqueue(
          encoder.encode(
            'data: {"type":"step_completed","step_number":1,"output":"Done","tool_called":"","cost_increment":0.001}\n\n'
          )
        );
        controller.enqueue(
          encoder.encode(
            'data: {"type":"simulation_complete","total_steps":1,"total_cost":0.001,"final_status":"complete"}\n\n'
          )
        );
        controller.close();
      },
    });

    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('simulation/stream')) {
        return { ok: true, body: stream, status: 200 };
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Test goal' } });
    fireEvent.click(screen.getByText('Run Simulation'));

    await waitFor(
      () => {
        expect(screen.getByText('Analyse goal requirements')).toBeDefined();
      },
      { timeout: 3000 }
    );
  });

  it('selected step detail appears in right inspector', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"type":"step_started","step_number":1,"description":"Fetch data from Jira","tool_called":"jira:search"}\n\n'
          )
        );
        controller.enqueue(
          encoder.encode(
            'data: {"type":"step_completed","step_number":1,"output":"[{id:1}]","tool_called":"jira:search","cost_increment":0.002}\n\n'
          )
        );
        controller.enqueue(
          encoder.encode(
            'data: {"type":"simulation_complete","total_steps":1,"total_cost":0.002,"final_status":"complete"}\n\n'
          )
        );
        controller.close();
      },
    });

    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('simulation/stream')) {
        return { ok: true, body: stream, status: 200 };
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Fetch Jira data' } });
    fireEvent.click(screen.getByText('Run Simulation'));

    const stepEl = await waitFor(() => screen.getByText('Fetch data from Jira'), {
      timeout: 3000,
    });
    fireEvent.click(stepEl);

    await waitFor(() => {
      expect(screen.getByText('jira:search')).toBeDefined();
    });
  });

  it('save scenario persists to scenario library', async () => {
    renderPage();

    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Save this as a scenario' } });

    const saveButton = screen.getByTitle('Save current');
    fireEvent.click(saveButton);

    const nameInput = await waitFor(() => screen.getByPlaceholderText(/scenario name/i));
    fireEvent.change(nameInput, { target: { value: 'My Test Scenario' } });

    // Click the Save button inside the dialog
    const saveButtons = screen.getAllByText('Save');
    fireEvent.click(saveButtons[saveButtons.length - 1]);

    await waitFor(() => {
      expect(screen.getByText('My Test Scenario')).toBeDefined();
    });
  });

  it('abort clears running state and shows Run Simulation again', async () => {
    const stream = new ReadableStream({
      start() {
        // Never pushes — keeps running
      },
    });

    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('simulation/stream')) {
        return { ok: true, body: stream, status: 200 };
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Test abort' } });
    fireEvent.click(screen.getByText('Run Simulation'));

    const abortButton = await waitFor(() => screen.getByText('Abort'));
    fireEvent.click(abortButton);

    await waitFor(() => {
      expect(screen.getByText('Run Simulation')).toBeDefined();
    });
  });

  it('shows Quick Templates section in left sidebar', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Quick Templates')).toBeDefined();
      expect(screen.getByText('GitHub PR Review')).toBeDefined();
      expect(screen.getByText('Slack Alert')).toBeDefined();
      expect(screen.getByText('Database Query')).toBeDefined();
    });
  });

  it('renders ToolCard for each available tool and toggles it on/off', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        tools: [{ name: 'jira:search', description: 'Search Jira', server_id: 'jira' }],
        total: 1,
      }),
      body: null,
      status: 200,
    });

    renderPage();

    const toggleBtn = await waitFor(() => screen.getByLabelText('Toggle jira:search'));
    fireEvent.click(toggleBtn);

    await waitFor(() => {
      expect(screen.getByText('1 active')).toBeDefined();
    });

    // Expand the enabled tool card to reveal the mock-output textarea
    // Find the chevron button next to the tool row (only visible once enabled)
    const chevron = screen.getByLabelText('Toggle jira:search').parentElement?.querySelector(
      'button:not([aria-label])'
    );
    expect(chevron).toBeTruthy();
    fireEvent.click(chevron!);

    const outputArea = await waitFor(() =>
      screen.getByDisplayValue('{"result": "mocked response"}')
    );
    fireEvent.change(outputArea, { target: { value: '{"custom":true}' } });
    expect((outputArea as HTMLTextAreaElement).value).toBe('{"custom":true}');

    // Toggle off again
    fireEvent.click(toggleBtn);
    await waitFor(() => {
      expect(screen.queryByText('1 active')).not.toBeInTheDocument();
    });
  });

  it('adds, edits, and removes a custom tool when no available tools exist', async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/no tools configured/i)).toBeDefined();
    });

    fireEvent.click(screen.getByText('Custom'));

    const nameInput = await waitFor(() => screen.getByPlaceholderText('tool_name'));
    expect((nameInput as HTMLInputElement).value).toMatch(/^custom:tool_/);

    fireEvent.change(nameInput, { target: { value: 'custom:renamed' } });
    expect((nameInput as HTMLInputElement).value).toBe('custom:renamed');

    const outputArea = screen.getByPlaceholderText('{"result": "mock"}');
    fireEvent.change(outputArea, { target: { value: '{"a":1}' } });
    expect((outputArea as HTMLTextAreaElement).value).toBe('{"a":1}');

    // Delete the row
    const nameRow = nameInput.closest('.flex.gap-2');
    const deleteBtn = nameRow?.querySelector('button');
    fireEvent.click(deleteBtn!);

    await waitFor(() => {
      expect(screen.getByText(/no tools configured/i)).toBeDefined();
    });
  });

  it('applies all quick templates and populates tools', async () => {
    renderPage();

    for (const name of ['GitHub PR Review', 'Slack Alert', 'Database Query']) {
      const btn = await waitFor(() => screen.getByText(name));
      fireEvent.click(btn);
      await waitFor(() => {
        const textarea = screen.getByPlaceholderText(
          /describe what the agent/i
        ) as HTMLTextAreaElement;
        expect(textarea.value.length).toBeGreaterThan(0);
      });
    }
  });

  it('New Scenario and Clear buttons reset goal, tools and steps', async () => {
    renderPage();

    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Something to clear' } });

    fireEvent.click(screen.getByText('New Scenario'));

    await waitFor(() => {
      const ta = screen.getByPlaceholderText(/describe what the agent/i) as HTMLTextAreaElement;
      expect(ta.value).toBe('');
    });

    fireEvent.change(screen.getByPlaceholderText(/describe what the agent/i), {
      target: { value: 'Another goal' },
    });
    fireEvent.click(screen.getByTitle('Clear all'));

    await waitFor(() => {
      const ta = screen.getByPlaceholderText(/describe what the agent/i) as HTMLTextAreaElement;
      expect(ta.value).toBe('');
    });
  });

  it('loads and deletes a saved scenario from the library', async () => {
    renderPage();

    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Scenario goal' } });
    fireEvent.click(screen.getByTitle('Save current'));

    const nameInput = await waitFor(() => screen.getByPlaceholderText(/scenario name/i));
    fireEvent.change(nameInput, { target: { value: 'Reload Me' } });
    const saveButtons = screen.getAllByText('Save');
    fireEvent.click(saveButtons[saveButtons.length - 1]);

    await waitFor(() => {
      expect(screen.getByText('Reload Me')).toBeDefined();
    });

    // Clear and reload via the saved scenario
    fireEvent.click(screen.getByText('New Scenario'));
    await waitFor(() => {
      const ta = screen.getByPlaceholderText(/describe what the agent/i) as HTMLTextAreaElement;
      expect(ta.value).toBe('');
    });

    fireEvent.click(screen.getByText('Reload Me'));
    await waitFor(() => {
      const ta = screen.getByPlaceholderText(/describe what the agent/i) as HTMLTextAreaElement;
      expect(ta.value).toBe('Scenario goal');
    });

    // Delete it — the trash icon is the sibling button in the same row
    const row = screen.getByText('Reload Me').closest('div.group');
    const trashBtn = row?.querySelectorAll('button')[1];
    fireEvent.click(trashBtn!);

    await waitFor(() => {
      expect(screen.queryByText('Reload Me')).not.toBeInTheDocument();
    });
  });

  it('saveScenario swallows a backend persistence failure', async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('/playground/scenarios')) {
        throw new Error('network down');
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Persist failure goal' } });
    fireEvent.click(screen.getByTitle('Save current'));

    const nameInput = await waitFor(() => screen.getByPlaceholderText(/scenario name/i));
    fireEvent.change(nameInput, { target: { value: 'Failing Scenario' } });
    fireEvent.keyDown(nameInput, { key: 'Enter' });

    await waitFor(() => {
      expect(screen.getByText('Failing Scenario')).toBeDefined();
    });
  });

  it('cancels the save-scenario dialog', async () => {
    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'goal' } });
    fireEvent.click(screen.getByTitle('Save current'));

    await waitFor(() => screen.getByPlaceholderText(/scenario name/i));
    fireEvent.click(screen.getByText('Cancel'));

    await waitFor(() => {
      expect(screen.queryByPlaceholderText(/scenario name/i)).not.toBeInTheDocument();
    });
  });

  it('toggles show-options (toolCalls, reasoning, costs) and filters reasoning steps', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"type":"step_started","step_number":1,"description":"Reasoning about the next move"}\n\n'
          )
        );
        controller.enqueue(
          encoder.encode(
            'data: {"type":"step_completed","step_number":1,"output":"thought","tool_called":"","cost_increment":0.001}\n\n'
          )
        );
        controller.enqueue(
          encoder.encode(
            'data: {"type":"simulation_complete","total_steps":1,"total_cost":0.001,"final_status":"complete"}\n\n'
          )
        );
        controller.close();
      },
    });

    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('simulation/stream')) {
        return { ok: true, body: stream, status: 200 };
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Reasoning goal' } });
    fireEvent.click(screen.getByText('Run Simulation'));

    // Wait for run to finish so the export/steps-shown header renders
    await waitFor(() => {
      expect(screen.getByText('Run Simulation')).toBeDefined();
    });

    // Reasoning step hidden by default (showOptions.reasoning=false)
    expect(screen.queryByText('Reasoning about the next move')).not.toBeInTheDocument();

    // Toggle reasoning on
    fireEvent.click(screen.getByText('Reasoning'));
    await waitFor(() => {
      expect(screen.getByText('Reasoning about the next move')).toBeDefined();
    });

    // Toggle toolCalls + costs off (branch coverage on show options)
    fireEvent.click(screen.getByRole('button', { name: 'Tool Calls' }));
    fireEvent.click(screen.getByRole('button', { name: 'Costs' }));

    // Select the reasoning step to inspect it — covers the "reasoning" type badge branch
    fireEvent.click(screen.getByText('Reasoning about the next move'));
    await waitFor(() => {
      expect(screen.getByText('Step 1')).toBeDefined();
    });
  });

  it('exports the simulation trace as a downloaded JSON file', async () => {
    const createObjectURL = vi.fn().mockReturnValue('blob:mock');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('simulation') && !String(url).includes('available')) {
        return {
          ok: true,
          json: async () => ({
            status: 'completed',
            steps: [{ step: 'Do the thing', tool: 'jira:search', output: 'ok' }],
            cost_usd: 0.01,
            iterations: 1,
          }),
          body: null,
        };
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Export trace goal' } });
    fireEvent.click(screen.getByText('Run Simulation'));

    const exportBtn = await waitFor(() => screen.getByText('Export Trace'), { timeout: 3000 });
    fireEvent.click(exportBtn);

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);

    clickSpy.mockRestore();
  });

  it('shows an error final-result badge when the simulation errors', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode('data: {"type":"step_started","step_number":1,"description":"Try the tool"}\n\n')
        );
        controller.enqueue(encoder.encode('data: {"type":"simulation_error"}\n\n'));
        controller.close();
      },
    });

    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('simulation/stream')) {
        return { ok: true, body: stream, status: 200 };
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Erroring goal' } });
    fireEvent.click(screen.getByText('Run Simulation'));

    await waitFor(() => {
      expect(screen.getByText('error')).toBeDefined();
    });
  });

  it('handles a non-abort fetch error without crashing', async () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('simulation/stream')) {
        throw new Error('boom');
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Erroring fetch goal' } });
    fireEvent.click(screen.getByText('Run Simulation'));

    await waitFor(() => {
      expect(screen.getByText('Run Simulation')).toBeDefined();
    });
    expect(consoleSpy).toHaveBeenCalledWith('Simulation error:', expect.any(Error));
    consoleSpy.mockRestore();
  });

  it('renders non-JSON step output as raw text in the inspector (JSON.parse catch branch)', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"type":"step_started","step_number":1,"description":"Verify the completed check"}\n\n'
          )
        );
        controller.enqueue(
          encoder.encode(
            'data: {"type":"step_completed","step_number":1,"output":"not-json-output","tool_called":"","cost_increment":0.001}\n\n'
          )
        );
        controller.enqueue(
          encoder.encode(
            'data: {"type":"simulation_complete","total_steps":1,"total_cost":0.001,"final_status":"complete"}\n\n'
          )
        );
        controller.close();
      },
    });

    mockFetch.mockImplementation(async (url: string) => {
      if (String(url).includes('simulation/stream')) {
        return { ok: true, body: stream, status: 200 };
      }
      return { ok: true, json: async () => ({ tools: [], total: 0 }), body: null };
    });

    renderPage();
    const textarea = await waitFor(() =>
      screen.getByPlaceholderText(/describe what the agent/i)
    );
    fireEvent.change(textarea, { target: { value: 'Verify goal' } });
    fireEvent.click(screen.getByText('Run Simulation'));

    const stepEl = await waitFor(() => screen.getByText('Verify the completed check'), {
      timeout: 3000,
    });
    fireEvent.click(stepEl);

    await waitFor(() => {
      const outputs = screen.getAllByText('not-json-output');
      expect(outputs.length).toBeGreaterThan(0);
    });
  });
});
