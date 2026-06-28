import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { A2APage } from './A2APage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <A2APage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

const CARD = {
  agent_id: 'agentverse-platform',
  name: 'AgentVerse Platform',
  version: '0.1.0',
  description: 'Agentic OS',
  endpoint: 'http://x/a2a',
  authentication: { scheme: 'hmac-sha256', header: 'X-A2A-Signature', note: '' },
  capabilities: ['goal_execution', 'audit_log'],
  supported_task_types: ['goal'],
};

describe('A2APage', () => {
  test('renders the agent card capabilities', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(CARD), { status: 200 })
    );
    renderPage();
    expect(await screen.findByText('AgentVerse Platform')).toBeInTheDocument();
    expect(screen.getByText('goal_execution')).toBeInTheDocument();
  });

  test('shows loading state before card loads', () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(new Response(JSON.stringify(CARD), { status: 200 })), 500))
    );
    renderPage();
    expect(screen.getByText(/loading agent card/i)).toBeInTheDocument();
  });

  test('task lookup fetches /a2a/tasks/{id} and shows status', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/.well-known/agent.json'))
        return new Response(JSON.stringify(CARD), { status: 200 });
      if (url.includes('/a2a/tasks/t1'))
        return new Response(
          JSON.stringify({ task_id: 't1', goal: 'Do thing', status: 'complete', result: 'done' }),
          { status: 200 }
        );
      return new Response('{}', { status: 200 });
    });
    renderPage();
    await screen.findByText('AgentVerse Platform');
    await userEvent.type(screen.getByLabelText(/task id/i), 't1');
    await userEvent.click(screen.getByRole('button', { name: /look up/i }));
    expect(await screen.findByText('Do thing')).toBeInTheDocument();
  });
});
