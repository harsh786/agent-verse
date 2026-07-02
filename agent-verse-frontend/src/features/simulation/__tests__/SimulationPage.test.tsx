import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
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
});
