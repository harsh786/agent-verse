import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentCreatePage } from './AgentCreatePage';
import { AgentDetailPage } from './AgentDetailPage';

// CORE-04: the agent's reasoning-pattern flags can be set on create, edited on
// the detail page, and are shown there.

const { mockNavigate } = vi.hoisted(() => ({ mockNavigate: vi.fn() }));
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => mockNavigate };
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

const AGENT = {
  agent_id: 'agent-001',
  name: 'Code Reviewer',
  autonomy_mode: 'supervised',
  goal_template: 'Review all open PRs',
  connector_ids: [],
  enable_cot: true,
  enable_debate: false,
  pattern_flags: { enable_cot: true, enable_debate: false },
};

describe('reasoning pattern flags', () => {
  beforeEach(() => {
    useAuthStore.setState({ apiKey: 'k', tenantId: 't', isAuthenticated: true });
    mockNavigate.mockClear();
  });
  afterEach(() => vi.restoreAllMocks());

  test('manual create sends the toggled flags', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      json({ agent_id: 'agent-new', name: 'Thinker', autonomy_mode: 'supervised' }, 201),
    );
    render(
      <QueryClientProvider client={client()}>
        <MemoryRouter>
          <AgentCreatePage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await userEvent.click(screen.getByRole('button', { name: /manual configuration/i }));
    await userEvent.type(screen.getByPlaceholderText('My Jira Agent'), 'Thinker');
    await userEvent.click(screen.getByRole('checkbox', { name: /chain-of-thought/i }));
    await userEvent.click(screen.getByRole('checkbox', { name: /debate/i }));
    await userEvent.click(screen.getByRole('button', { name: 'Create Agent' }));

    await waitFor(() => {
      const post = spy.mock.calls.find(([u, i]) =>
        String(u).endsWith('/agents') && (i as RequestInit)?.method === 'POST');
      expect(post).toBeTruthy();
      const body = JSON.parse(String((post?.[1] as RequestInit).body));
      expect(body.enable_cot).toBe(true);
      expect(body.enable_debate).toBe(true);
      expect(body.enable_supervisor).toBe(false);
    });
  });

  test('detail page shows enabled patterns and saves an edited flag', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/connectors')) return json([]);
      if (url.includes('/versions')) return json([]);
      if (url.includes('/goals')) return json({ goals: [] });
      return json(AGENT);
    });
    render(
      <MemoryRouter initialEntries={['/agents/agent-001']}>
        <QueryClientProvider client={client()}>
          <Routes>
            <Route path="/agents/:agentId" element={<AgentDetailPage />} />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await screen.findByTestId('agent-name');
    expect(screen.getByTestId('reasoning-patterns')).toHaveTextContent(/chain-of-thought/i);
    expect(screen.getByTestId('reasoning-patterns')).not.toHaveTextContent(/debate/i);

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const debate = await screen.findByRole('checkbox', { name: /debate/i });
    expect(debate).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /chain-of-thought/i })).toBeChecked();
    await userEvent.click(debate);
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      const put = spy.mock.calls.find(([u, i]) =>
        String(u).includes('/agents/agent-001') && (i as RequestInit)?.method === 'PUT');
      expect(put).toBeTruthy();
      const body = JSON.parse(String((put?.[1] as RequestInit).body));
      expect(body.enable_debate).toBe(true);
      expect(body.enable_cot).toBe(true);
    });
  });
});
