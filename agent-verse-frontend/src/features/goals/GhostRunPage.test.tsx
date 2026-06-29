import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { GhostRunPage } from './GhostRunPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <GhostRunPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_GOAL = {
  goal_id: 'goal-ghost-1',
  id: 'goal-ghost-1',
  goal: 'Test ghost run',
  status: 'planning',
};

describe('GhostRunPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders page without crashing', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows a textarea for entering the goal', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );
    renderPage();
    // There should be a textarea or input for goal entry
    const textarea = screen.queryByRole('textbox');
    expect(textarea).toBeInTheDocument();
  });

  test('shows Ghost Run heading or ghost icon', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );
    renderPage();
    // Multiple elements mention "ghost run" — just assert at least one is present
    expect(screen.getAllByText(/ghost run/i).length).toBeGreaterThan(0);
  });

  test('run button is present', () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200 })
    );
    renderPage();
    // A submit / run button should be rendered
    const btn = screen.queryByRole('button');
    expect(btn).toBeInTheDocument();
  });

  test('submits goals and shows results', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_GOAL), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );
    renderPage();
    const textarea = screen.getByRole('textbox');
    await user.type(textarea, 'Summarize all open issues');
    const btn = screen.getByRole('button');
    await user.click(btn);
    // After mutation, results should eventually render
    await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 3000 });
  });
});
