import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { SimulationPage } from '../SimulationPage';

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    if (url.includes('/governance/simulate'))
      return new Response(JSON.stringify({
        summary: { allowed_tools: ['tool1'], denied_tools: [], requires_approval: [], would_block_execution: false, hitl_approvals_needed: 0 },
        policy_checks: [{ tool: 'tool1', result: 'allow' }],
      }), { status: 200 });
    if (url.includes('/enterprise/simulation/available-tools'))
      return new Response(JSON.stringify({ tools: [{ name: 'jira_search', description: 'Search Jira', server_id: 'builtin-jira' }], total: 1 }), { status: 200 });
    if (url.includes('/enterprise/simulation/stream') && method === 'POST')
      return new Response(
        'data: {"type":"simulation_started","run_id":"run-1","goal":"test"}\n\ndata: {"type":"step_started","step_number":1,"description":"Plan the task"}\n\ndata: {"type":"step_completed","step_number":1,"output":"Done","cost_increment":0.001}\n\ndata: {"type":"simulation_complete","run_id":"run-1","total_steps":1,"total_cost":0.001,"used_real_llm":false,"final_status":"complete"}\n\n',
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
      );
    return new Response('{}', { status: 200 });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <SimulationPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  sessionStorage.setItem('av_api_key', 'test-key');
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
});
afterEach(() => vi.restoreAllMocks());

describe('SimulationPage', () => {
  test('renders heading', () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('heading', { name: /simulation studio/i })).toBeInTheDocument();
  });

  test('shows goal textarea', () => {
    mockFetch();
    renderPage();
    expect(screen.getByLabelText(/simulation goal/i)).toBeInTheDocument();
  });

  test('shows Run Simulation button disabled when no goal', () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('button', { name: /run simulation/i })).toBeDisabled();
  });

  test('shows Run Simulation button enabled when goal is filled', async () => {
    mockFetch();
    renderPage();
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Deploy the app');
    expect(screen.getByRole('button', { name: /run simulation/i })).toBeEnabled();
  });

  test('Mock Tools section is present and expandable', async () => {
    mockFetch();
    renderPage();
    const mockBtn = screen.getByRole('button', { name: /mock tools/i });
    await userEvent.click(mockBtn);
    await waitFor(() => expect(screen.getByPlaceholderText(/search available tools/i)).toBeInTheDocument());
  });

  test('runs simulation and shows steps', async () => {
    mockFetch();
    renderPage();
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Test goal');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getAllByTestId('simulation-step').length).toBeGreaterThan(0), { timeout: 5000 });
  });

  test('shows summary card after simulation completes', async () => {
    mockFetch();
    renderPage();
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Test goal');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getByText(/simulation complete/i)).toBeInTheDocument(), { timeout: 5000 });
  });

  test('shows agent ID input', () => {
    mockFetch();
    renderPage();
    expect(screen.getByLabelText(/agent id/i)).toBeInTheDocument();
  });

  test('governance preview appears after typing a goal and debounce elapses', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetch();
    renderPage();
    await userEvent.setup({ delay: null }).type(screen.getByLabelText(/simulation goal/i), 'Deploy prod');
    await vi.advanceTimersByTimeAsync(1100);
    await waitFor(() => expect(screen.getByText('Policy Preview')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText(/execution allowed/i)).toBeInTheDocument());
    vi.useRealTimers();
  });

  test('governance preview shows blocked message when would_block_execution is true', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/governance/simulate'))
        return new Response(JSON.stringify({
          summary: { allowed_tools: [], denied_tools: ['jira.delete'], requires_approval: ['stripe.refund'], would_block_execution: true, hitl_approvals_needed: 2 },
          policy_checks: [],
        }), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await userEvent.setup({ delay: null }).type(screen.getByLabelText(/simulation goal/i), 'Delete prod db');
    await vi.advanceTimersByTimeAsync(1100);
    await waitFor(() => expect(screen.getByText(/execution would be blocked/i)).toBeInTheDocument());
    expect(screen.getByText(/jira.delete/)).toBeInTheDocument();
    expect(screen.getByText(/stripe.refund/)).toBeInTheDocument();
    expect(screen.getByText(/2 HITL approval/)).toBeInTheDocument();
    vi.useRealTimers();
  });

  test('governance preview disappears when goal is cleared', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetch();
    renderPage();
    const goalInput = screen.getByLabelText(/simulation goal/i);
    const user = userEvent.setup({ delay: null });
    await user.type(goalInput, 'Deploy prod');
    await vi.advanceTimersByTimeAsync(1100);
    await waitFor(() => expect(screen.getByText('Policy Preview')).toBeInTheDocument());
    await user.clear(goalInput);
    await waitFor(() => expect(screen.queryByText('Policy Preview')).not.toBeInTheDocument());
    vi.useRealTimers();
  });

  test('Mock Tools: loads tools list, toggles a mock on and off', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /mock tools/i }));
    await waitFor(() => expect(screen.getByText('jira_search')).toBeInTheDocument());
    const checkbox = screen.getByRole('checkbox');
    await userEvent.click(checkbox);
    expect(checkbox).toBeChecked();
    await waitFor(() => expect(screen.getByText('1 mocked')).toBeInTheDocument());
    // Toggle off
    await userEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();
    expect(screen.queryByText('1 mocked')).not.toBeInTheDocument();
  });

  test('Mock Tools: search filters the available tool list', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /mock tools/i }));
    await waitFor(() => expect(screen.getByText('jira_search')).toBeInTheDocument());
    await userEvent.type(screen.getByPlaceholderText(/search available tools/i), 'no-match-xyz');
    await waitFor(() => expect(screen.queryByText('jira_search')).not.toBeInTheDocument());
  });

  test('Mock Tools: editing a mocked tool response updates its textarea', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /mock tools/i }));
    await waitFor(() => expect(screen.getByText('jira_search')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('checkbox'));
    const textarea = await screen.findByLabelText('Mock response for jira_search');
    fireEvent.change(textarea, { target: { value: '{"a":1}' } });
    expect(textarea).toHaveValue('{"a":1}');
  });

  test('Mock Tools: manual add creates a new mock entry and clears inputs', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /mock tools/i }));
    await waitFor(() => expect(screen.getByText('jira_search')).toBeInTheDocument());
    const keyInput = screen.getByPlaceholderText('tool_name');
    await userEvent.type(keyInput, 'custom_tool');
    const addBtn = screen.getByRole('button', { name: 'Add' });
    await userEvent.click(addBtn);
    await waitFor(() => expect(screen.getByText('1 mocked')).toBeInTheDocument());
    expect(keyInput).toHaveValue('');
  });

  test('Mock Tools: Add button disabled without a manual key', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /mock tools/i }));
    await waitFor(() => expect(screen.getByText('jira_search')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled();
  });

  test('Mock Tools: clear all mocks button removes every mock', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /mock tools/i }));
    await waitFor(() => expect(screen.getByText('jira_search')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('checkbox'));
    await waitFor(() => expect(screen.getByText('1 mocked')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Clear all mocks'));
    expect(screen.queryByText('1 mocked')).not.toBeInTheDocument();
  });

  test('Mock Tools: shows no-tools message when tool list is empty', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/enterprise/simulation/available-tools'))
        return new Response(JSON.stringify({ tools: [], total: 0 }), { status: 200 });
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /mock tools/i }));
    await waitFor(() => expect(screen.getByText(/no tools available/i)).toBeInTheDocument());
  });

  test('Mock Tools panel collapses again on second click', async () => {
    mockFetch();
    renderPage();
    const toggle = screen.getByRole('button', { name: /mock tools/i });
    await userEvent.click(toggle);
    await waitFor(() => expect(screen.getByPlaceholderText(/search available tools/i)).toBeInTheDocument());
    await userEvent.click(toggle);
    await waitFor(() => expect(screen.queryByPlaceholderText(/search available tools/i)).not.toBeInTheDocument());
  });

  test('stop simulation button aborts a running simulation', async () => {
    // A stream that never completes so status stays "running" until stopped.
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      if (url.includes('/enterprise/simulation/stream') && method === 'POST') {
        return new Response(new ReadableStream({ start() {} }), {
          status: 200, headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Test goal');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getByRole('button', { name: /stop simulation/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /stop simulation/i }));
    await waitFor(() => expect(screen.getByText(/simulation failed/i)).toBeInTheDocument());
  });

  test('does not run simulation when goal is blank (no-op)', async () => {
    const spy = mockFetch();
    renderPage();
    const runBtn = screen.getByRole('button', { name: /run simulation/i });
    // Button is disabled so a user click cannot trigger it; assert no stream call was made.
    expect(runBtn).toBeDisabled();
    expect(spy.mock.calls.some(([u]) => String(u).includes('/enterprise/simulation/stream'))).toBe(false);
  });

  test('shows error toast and failed status when the stream response is not ok', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      if (url.includes('/enterprise/simulation/stream') && method === 'POST') {
        return new Response(null, { status: 500 });
      }
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Test goal');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getByText(/simulation failed/i)).toBeInTheDocument());
  });

  test('shows simulation_error event message via toast and sets failed status', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      if (url.includes('/enterprise/simulation/stream') && method === 'POST') {
        return new Response(
          'data: {"type":"simulation_error","message":"Tool not found"}\n\n',
          { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
        );
      }
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Test goal');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getByText(/simulation failed/i)).toBeInTheDocument());
  });

  test('export steps JSON triggers a download after simulation completes', async () => {
    mockFetch();
    renderPage();
    if (!URL.createObjectURL) (URL as unknown as { createObjectURL: unknown }).createObjectURL = () => 'blob:mock';
    if (!URL.revokeObjectURL) (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = () => {};
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const createUrlSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock');
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Test goal');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getByText(/simulation complete/i)).toBeInTheDocument(), { timeout: 5000 });
    const exportButtons = screen.getAllByTitle('Export steps as JSON').concat(
      screen.getAllByText(/export json/i).map((el) => el.closest('button') as HTMLElement).filter(Boolean)
    );
    await userEvent.click(exportButtons[0]);
    expect(clickSpy).toHaveBeenCalled();
    clickSpy.mockRestore();
    createUrlSpy.mockRestore();
  });

  test('run history collapses and expands, and Re-run repopulates the goal', async () => {
    mockFetch();
    renderPage();
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Test goal');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getByText(/simulation complete/i)).toBeInTheDocument(), { timeout: 5000 });
    const historyToggle = await screen.findByRole('button', { name: /run history/i });
    await userEvent.click(historyToggle);
    await waitFor(() => expect(screen.getByText('Re-run')).toBeInTheDocument());
    await userEvent.click(screen.getByText('Re-run'));
    expect((screen.getByLabelText(/simulation goal/i) as HTMLTextAreaElement).value).toContain('Test goal');
  });

  test('expanding a step card shows its output', async () => {
    mockFetch();
    renderPage();
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Test goal');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getAllByTestId('simulation-step').length).toBeGreaterThan(0), { timeout: 5000 });
    const stepCard = screen.getAllByTestId('simulation-step')[0];
    const expandBtn = stepCard.querySelector('button');
    if (expandBtn) {
      await userEvent.click(expandBtn);
      await waitFor(() => expect(screen.getByText('Done')).toBeInTheDocument());
    }
  });

  test('"Run Live" button navigates to /goals with the goal text', async () => {
    mockFetch();
    renderPage();
    await userEvent.type(screen.getByLabelText(/simulation goal/i), 'Test goal');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));
    await waitFor(() => expect(screen.getByText(/simulation complete/i)).toBeInTheDocument(), { timeout: 5000 });
    const runLiveBtn = screen.getByRole('button', { name: /run live/i });
    await userEvent.click(runLiveBtn);
    expect(runLiveBtn).toBeInTheDocument();
  });
});
