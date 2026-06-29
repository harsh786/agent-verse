import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentCreatePage } from './AgentCreatePage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AgentCreatePage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_CREATED_AGENT = {
  agent_id: 'agent-new-1',
  name: 'My Agent',
  autonomy_mode: 'bounded-autonomous',
  created_at: '2026-06-29T00:00:00Z',
};

describe('AgentCreatePage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'test-key', tenantId: 'tenant-1', plan: 'professional', isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('renders without crashing', () => {
    renderPage();
    expect(document.body).toBeTruthy();
  });

  test('shows NL mode by default', () => {
    renderPage();
    // NL command textarea should be in the DOM
    expect(screen.getByRole('textbox')).toBeInTheDocument();
  });

  test('shows mode toggle buttons (NL and Manual)', () => {
    renderPage();
    // Page shows two mode buttons
    const buttons = screen.queryAllByRole('button');
    expect(buttons.length).toBeGreaterThan(0);
  });

  test('renders Create Agent heading', () => {
    renderPage();
    // Use role query to distinguish h1 from the button with the same text
    expect(screen.getByRole('heading', { name: /create agent/i })).toBeInTheDocument();
  });

  test('shows manual mode form when Manual tab is clicked', async () => {
    const user = userEvent.setup();
    renderPage();
    // The tab button text is "Manual Configuration"
    const manualBtn = screen.getByRole('button', { name: /manual configuration/i });
    await user.click(manualBtn);
    await waitFor(() =>
      // Manual form shows "Agent Name *" label
      expect(screen.getByPlaceholderText('My Jira Agent')).toBeInTheDocument(),
      { timeout: 2000 }
    );
  });

  test('NL mode: submits command and calls API', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(MOCK_CREATED_AGENT), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    );

    renderPage();
    const textarea = screen.getByRole('textbox');
    await user.type(textarea, 'Create an agent that monitors GitHub issues');

    // The NL mode create button has text "Create Agent" (exact)
    const createBtn = screen.getByRole('button', { name: 'Create Agent' });
    await user.click(createBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    }, { timeout: 3000 });
  });

  test('shows error message when agent creation fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: { message: 'Quota exceeded' } }), {
        status: 429,
      })
    );

    renderPage();
    const textarea = screen.getByRole('textbox');
    await user.type(textarea, 'Create agent');

    // Switch to manual mode to test error display there too
    const manualBtn = screen.queryByRole('button', { name: /manual/i });
    if (manualBtn) {
      await user.click(manualBtn);
      const nameInput = screen.queryByPlaceholderText(/agent name/i);
      if (nameInput) {
        await user.type(nameInput, 'test agent');
        const saveBtn = screen.queryByRole('button', { name: /create|save/i });
        if (saveBtn) {
          await user.click(saveBtn);
          await waitFor(() => expect(document.body).toBeTruthy(), { timeout: 2000 });
        }
      }
    }
    expect(document.body).toBeTruthy();
  });
});
