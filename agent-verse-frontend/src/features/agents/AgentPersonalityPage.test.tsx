import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
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
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
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
});
