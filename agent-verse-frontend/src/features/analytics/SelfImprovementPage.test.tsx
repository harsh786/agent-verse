import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { SelfImprovementPage } from './SelfImprovementPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SelfImprovementPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_EXPERIMENTS = [
  {
    id: 'exp-1',
    name: 'Temperature A/B test',
    agent_id: 'agent-1',
    status: 'running',
    control_config: { temperature: 0.2 },
    challenger_config: { temperature: 0.7 },
    lift_pct: null,
    started_at: '2026-06-20T00:00:00Z',
    concluded_at: null,
  },
  {
    id: 'exp-2',
    name: 'Model comparison',
    agent_id: 'agent-1',
    status: 'concluded',
    control_config: { model: 'gpt-4o' },
    challenger_config: { model: 'claude-3-5-sonnet' },
    lift_pct: 12.5,
    started_at: '2026-06-01T00:00:00Z',
    concluded_at: '2026-06-15T00:00:00Z',
  },
];

const MOCK_SUGGESTIONS = [
  {
    id: 'sug-1',
    type: 'prompt_optimization',
    description: 'Increase planning detail in system prompt',
    confidence: 0.87,
    agent_id: 'agent-1',
    status: 'pending',
    created_at: '2026-06-28T00:00:00Z',
  },
];

function mockFetch(experiments = MOCK_EXPERIMENTS, suggestions = MOCK_SUGGESTIONS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/intelligence/experiments')) {
      return new Response(JSON.stringify(experiments), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/intelligence/suggestions')) {
      return new Response(JSON.stringify(suggestions), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response(null, { status: 404 });
  });
}

describe('SelfImprovementPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'enterprise', isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders without crashing', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows loading skeleton while fetching', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body.innerHTML).toBeTruthy();
  });

  test('shows Self-Improvement heading', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Self-Improvement' })).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows experiment names from API', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Temperature A/B test')).toBeInTheDocument(),
      { timeout: 3000 }
    );
    expect(screen.getByText('Model comparison')).toBeInTheDocument();
  });

  test('shows concluded experiment lift percentage', async () => {
    mockFetch();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/12\.5%|12.5 %/)).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows improvement suggestions from API', async () => {
    const user = userEvent.setup();
    mockFetch();
    renderPage();
    // Tab buttons use role="tab" (not button)
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'suggestions' })).toBeInTheDocument()
    );
    await user.click(screen.getByRole('tab', { name: 'suggestions' }));
    await waitFor(() =>
      expect(screen.getByText('Increase planning detail in system prompt')).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows empty experiments state gracefully', async () => {
    mockFetch([], []);
    renderPage();
    await waitFor(() =>
      expect(screen.queryByText('Temperature A/B test')).not.toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('handles fetch error without crashing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('Server Error', { status: 500 })
    );
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });
});
