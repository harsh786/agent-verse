import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { EvalPage } from './EvalPage';

function renderEvalPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <EvalPage />
    </QueryClientProvider>
  );
}

/**
 * Default fetch mock that satisfies all EvalPage on-mount queries
 * (EvalScorerSection loads /goals and /intelligence/suggestions on mount).
 * Pass overrides to customise the response for specific endpoints.
 */
function makeEvalFetch(overrides: {
  redTeam?: object;
  simulation?: object;
} = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);

    // Mutations
    if (url.endsWith('/enterprise/red-team') && init?.method === 'POST') {
      return new Response(
        JSON.stringify(overrides.redTeam ?? { total: 0, passed: 0, failed: 0, results: [] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.endsWith('/enterprise/simulation') && init?.method === 'POST') {
      return new Response(
        JSON.stringify(overrides.simulation ?? { status: 'complete', steps: [] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }

    // EvalScorerSection on-mount GET queries
    if (url.endsWith('/goals') || url.includes('/goals?')) {
      return new Response(
        JSON.stringify({ goals: [] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.includes('/intelligence/suggestions')) {
      return new Response(
        JSON.stringify([]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }

    return new Response(null, { status: 404 });
  });
}

describe('EvalPage – Red Team section', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'enterprise',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders Red Team and Simulation section headings', () => {
    makeEvalFetch();
    renderEvalPage();
    expect(screen.getByText('Red Team Testing')).toBeInTheDocument();
    expect(screen.getByText('Goal Simulation')).toBeInTheDocument();
  });

  test('shows page title', () => {
    makeEvalFetch();
    renderEvalPage();
    expect(screen.getByText('Eval & Testing')).toBeInTheDocument();
  });

  test('Run Red Team button calls the red team API', async () => {
    const fetchMock = makeEvalFetch();

    renderEvalPage();
    await userEvent.click(screen.getByRole('button', { name: /run red team/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/enterprise\/red-team$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
  });

  test('shows pass and fail counts after red team run', async () => {
    makeEvalFetch({
      redTeam: {
        total: 5,
        passed: 4,
        failed: 1,
        results: [
          { case_id: 'c1', name: 'Prompt injection', status: 'passed' },
          { case_id: 'c2', name: 'Policy bypass', status: 'failed', details: 'Policy not enforced' },
        ],
      },
    });

    renderEvalPage();
    await userEvent.click(screen.getByRole('button', { name: /run red team/i }));

    await waitFor(() => expect(screen.getByText('Total Cases')).toBeInTheDocument());
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });

  test('shows result rows with pass/fail status badges', async () => {
    makeEvalFetch({
      redTeam: {
        total: 2,
        passed: 1,
        failed: 1,
        results: [
          { case_id: 'c1', name: 'Injection test', status: 'passed' },
          { case_id: 'c2', name: 'Bypass test', status: 'failed', details: 'bypass detected' },
        ],
      },
    });

    renderEvalPage();
    await userEvent.click(screen.getByRole('button', { name: /run red team/i }));
    await waitFor(() => expect(screen.getByText('Injection test')).toBeInTheDocument());
    expect(screen.getByText('Bypass test')).toBeInTheDocument();
    expect(screen.getByText('passed')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });
});

describe('EvalPage – Simulation section', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      apiKey: 'tenant-key',
      tenantId: 'tenant-1',
      plan: 'enterprise',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('Run Simulation button is disabled when goal is empty', () => {
    makeEvalFetch();
    renderEvalPage();
    const btn = screen.getByRole('button', { name: /run simulation/i });
    expect(btn).toBeDisabled();
  });

  test('Run Simulation button is enabled after typing a goal', async () => {
    makeEvalFetch();
    renderEvalPage();
    await userEvent.type(screen.getByPlaceholderText(/describe the goal to simulate/i), 'Deploy app');
    expect(screen.getByRole('button', { name: /run simulation/i })).not.toBeDisabled();
  });

  test('simulation submits goal and mock tools to the API', async () => {
    const fetchMock = makeEvalFetch({ simulation: { status: 'complete', steps: [] } });

    renderEvalPage();
    await userEvent.type(
      screen.getByPlaceholderText(/describe the goal to simulate/i),
      'Fix the bug'
    );
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/\/enterprise\/simulation$/),
        expect.objectContaining({ method: 'POST' })
      )
    );
    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).includes('/enterprise/simulation')
    );
    const body = JSON.parse(String(call?.[1]?.body));
    expect(body.goal).toBe('Fix the bug');
  });

  test('shows simulation steps on success', async () => {
    makeEvalFetch({
      simulation: {
        status: 'complete',
        steps: [
          { step: 'Analyse issue', tool: 'github:list_issues' },
          { step: 'Create fix', tool: 'github:create_pr' },
        ],
      },
    });

    renderEvalPage();
    await userEvent.type(
      screen.getByPlaceholderText(/describe the goal to simulate/i),
      'Fix a bug'
    );
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));

    await waitFor(() => expect(screen.getByText('Simulated Steps')).toBeInTheDocument());
    // Step spans are "1. Analyse issue" and "2. Create fix" — unique vs the raw JSON pre
    expect(screen.getByText(/1\. Analyse issue/)).toBeInTheDocument();
    expect(screen.getByText(/2\. Create fix/)).toBeInTheDocument();
  });

  test('shows error when mock_tools JSON is invalid', async () => {
    makeEvalFetch();
    renderEvalPage();

    await userEvent.type(
      screen.getByPlaceholderText(/describe the goal to simulate/i),
      'Fix something'
    );
    // Clear the mock tools field and enter invalid JSON
    // Note: '{' must be escaped as '{{' in userEvent v14
    const mockToolsArea = screen.getByPlaceholderText(
      /\{"github:list_issues":/i
    );
    await userEvent.clear(mockToolsArea);
    await userEvent.type(mockToolsArea, '{{invalid json');
    await userEvent.click(screen.getByRole('button', { name: /run simulation/i }));

    await waitFor(() =>
      expect(screen.getByText(/mock_tools must be valid json/i)).toBeInTheDocument()
    );
  });
});
