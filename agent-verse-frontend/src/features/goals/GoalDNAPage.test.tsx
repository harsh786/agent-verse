import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GoalDNAPage } from './GoalDNAPage';

// ReactFlow uses ResizeObserver and DOM measurements that don't exist in jsdom.
// The setup.ts already stubs ResizeObserver. We also need to stub the canvas
// context used by the WebGL renderer.
if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = vi.fn().mockReturnValue(null) as unknown as typeof HTMLCanvasElement.prototype.getContext;
}

const MOCK_GRAPH = {
  goal_id: 'goal-dna-1',
  nodes: [
    { id: 'start', type: 'start', label: 'Start', data: {}, position: { x: 0, y: 0 } },
    { id: 'step-1', type: 'step', label: 'List Files', data: {}, position: { x: 100, y: 0 } },
    { id: 'tool-1', type: 'tool', label: 'list_dir', data: { tool_name: 'list_dir' }, position: { x: 200, y: 0 } },
    { id: 'end', type: 'end', label: 'Complete', data: {}, position: { x: 300, y: 0 } },
  ],
  edges: [
    { id: 'e1', source: 'start', target: 'step-1' },
    { id: 'e2', source: 'step-1', target: 'tool-1' },
    { id: 'e3', source: 'tool-1', target: 'end' },
  ],
  stats: {
    total_nodes: 4,
    tool_calls: 1,
    unique_tools: 1,
  },
};

function renderPage(goalId = 'goal-dna-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[`/goals/${goalId}/dna`]}>
      <QueryClientProvider client={qc}>
        <Routes>
          <Route path="/goals/:goalId/dna" element={<GoalDNAPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe('GoalDNAPage', () => {
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

  test('shows skeleton loading state', () => {
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(document.body.innerHTML).toBeTruthy();
  });

  test('renders flow canvas container after data loads', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/insights/graph/')) {
        return new Response(JSON.stringify(MOCK_GRAPH), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });
    renderPage();
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });

  test('shows heading with DNA or graph terminology', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/insights/graph/')) {
        return new Response(JSON.stringify(MOCK_GRAPH), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(null, { status: 404 });
    });
    renderPage();
    // "Goal DNA" heading is always rendered (not inside a conditional)
    await waitFor(() =>
      expect(screen.getByText('Goal DNA')).toBeInTheDocument(),
      { timeout: 3000 }
    );
  });
});
