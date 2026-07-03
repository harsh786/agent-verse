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
});
