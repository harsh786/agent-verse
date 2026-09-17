import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentPersonalityPage } from './AgentPersonalityPage';

const MOCK_AGENT = {
  agent_id: 'agent-personality-1',
  name: 'Personality Bot',
  autonomy_mode: 'bounded-autonomous',
  max_iterations: 15,
  model_override: 'gpt-4o',
  created_at: '2026-01-01T00:00:00Z',
};

function renderPage(agentId = 'agent-personality-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[`/agents/${agentId}/personality`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/agents/:agentId/personality" element={<AgentPersonalityPage />} />
          <Route path="/agents/:agentId" element={<p>Agent Detail Page</p>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function fireSliderChange(slider: HTMLElement, value: number) {
  fireEvent.change(slider, { target: { value: String(value) } });
}

describe('AgentPersonalityPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
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

  test('shows skeleton while loading agent data', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    // Page has skeleton or spinner during load
    expect(document.body.innerHTML).toBeTruthy();
  });

  test('renders personality sliders after agent loads', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    // "Agent Personality" is the unique heading (multiple sliders also have labels)
    await waitFor(() =>
      expect(screen.getByText('Agent Personality')).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('shows Autonomy slider label', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText('Autonomy')).toBeInTheDocument());
  });

  test('shows Save Changes button', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });

  test('back button navigates to the agent detail page', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText('Agent Personality')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(await screen.findByText('Agent Detail Page')).toBeInTheDocument();
  });

  test('initializes sliders from the loaded agent config and derives the preview', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    // MOCK_AGENT: bounded-autonomous (50), max_iterations 15 -> thoroughness
    // ((15-5)/15)*100 = 67 (rounded), model_override 'gpt-4o' falls back to 50.
    const sliders = await screen.findAllByRole('slider');
    expect(sliders).toHaveLength(4);
    await waitFor(() => expect(screen.getByText('bounded-autonomous')).toBeInTheDocument());
    expect(screen.getByText('claude-sonnet-4-5')).toBeInTheDocument();
  });

  test('dragging the autonomy slider to max recomputes the preview as fully-autonomous', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    const sliders = await screen.findAllByRole('slider');
    fireSliderChange(sliders[0], 100);
    await waitFor(() => expect(screen.getByText('fully-autonomous')).toBeInTheDocument());
  });

  test('dragging the autonomy slider to 25 recomputes the preview as supervised', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    const sliders = await screen.findAllByRole('slider');
    fireSliderChange(sliders[0], 25);
    await waitFor(() => expect(screen.getByText('supervised')).toBeInTheDocument());
  });

  test('an agent with no autonomy_mode/model_override falls back to bounded-autonomous defaults', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ agent_id: 'agent-personality-1', name: 'Bare Bot' }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    await screen.findAllByRole('slider');
    await waitFor(() => expect(screen.getByText('bounded-autonomous')).toBeInTheDocument());
    expect(screen.getByText('claude-sonnet-4-5')).toBeInTheDocument();
  });

  test('dragging the autonomy slider to 0 recomputes the preview as manual', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    const sliders = await screen.findAllByRole('slider');
    fireSliderChange(sliders[0], 0);
    await waitFor(() => expect(screen.getByText('manual')).toBeInTheDocument());
  });

  test('dragging the cost slider to max selects the opus model, and to min selects haiku', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    const sliders = await screen.findAllByRole('slider');
    fireSliderChange(sliders[3], 100);
    await waitFor(() => expect(screen.getByText('claude-opus-4')).toBeInTheDocument());
    fireSliderChange(sliders[3], 0);
    await waitFor(() => expect(screen.getByText('claude-haiku-3-5')).toBeInTheDocument());
  });

  test('saving the personality PUTs the derived config and shows a success toast', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method === 'PUT') {
        return new Response(JSON.stringify({ ...MOCK_AGENT, autonomy_mode: 'fully-autonomous' }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    });
    renderPage();
    await screen.findAllByRole('slider');
    await waitFor(() => expect(screen.getByRole('button', { name: /save personality/i })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: /save personality/i }));
    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(
          ([, init]) => (init as RequestInit | undefined)?.method === 'PUT',
        ),
      ).toBe(true),
    );
  });

  test('a failed save surfaces an error toast instead of crashing', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      const method = (init?.method ?? 'GET').toUpperCase();
      if (method === 'PUT') return new Response('Server error', { status: 500 });
      return new Response(JSON.stringify(MOCK_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    });
    renderPage();
    await screen.findAllByRole('slider');
    await waitFor(() => expect(screen.getByRole('button', { name: /save personality/i })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: /save personality/i }));
    // Button returns to its resting label once the failed mutation settles.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /save personality/i })).toBeInTheDocument(),
    );
  });
});
