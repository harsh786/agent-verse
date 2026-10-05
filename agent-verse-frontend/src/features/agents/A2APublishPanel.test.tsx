import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { AgentResponse } from '@/lib/api/client';
import { A2APublishPanel } from './A2APublishPanel';

const AGENT: AgentResponse = {
  agent_id: 'ag1', name: 'Billing bot', autonomy_mode: 'supervised',
  system_prompt: 'SECRET-PROMPT', a2a_public: false, a2a_description: '', a2a_skills: [],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function renderPanel(agent: AgentResponse = AGENT) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <A2APublishPanel agent={agent} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('A2APublishPanel', () => {
  test('is off by default and says what becomes public', () => {
    renderPanel();
    expect(screen.getByRole('switch', { name: /public A2A directory/i })).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText(/tenant's A2A directory is on/i)).toBeInTheDocument();
  });

  test('saving publishes the opt-in with only the card fields', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ ...AGENT, a2a_public: true }));
    renderPanel();
    await userEvent.click(screen.getByRole('switch', { name: /public A2A directory/i }));
    await userEvent.type(screen.getByLabelText(/Public description/i), 'Answers billing questions');
    await userEvent.type(screen.getByLabelText(/Skills/i), 'billing, invoices, billing');
    await userEvent.click(screen.getByRole('button', { name: /Save directory settings/i }));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toMatch(/\/agents\/ag1$/);
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(String(init?.body))).toEqual({
      a2a_public: true,
      a2a_description: 'Answers billing questions',
      a2a_skills: ['billing', 'invoices'],
    });
  });

  test('too many skills block the save', async () => {
    const spy = vi.spyOn(globalThis, 'fetch');
    renderPanel();
    const many = Array.from({ length: 21 }, (_, i) => `s${i}`).join(',');
    await userEvent.type(screen.getByLabelText(/Skills/i), many);
    expect(screen.getByRole('alert')).toHaveTextContent(/At most 20 skills/);
    expect(screen.getByRole('button', { name: /Save directory settings/i })).toBeDisabled();
    expect(spy).not.toHaveBeenCalled();
  });

  test('a refused save is shown, not swallowed', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ detail: 'Agent store unavailable' }, 503));
    renderPanel({ ...AGENT, a2a_public: true });
    await userEvent.click(screen.getByRole('button', { name: /Save directory settings/i }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
  });
});
