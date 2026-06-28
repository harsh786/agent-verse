import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, expect, test, beforeEach, afterEach } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { AgentsListPage } from './AgentsListPage';

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

afterEach(() => {
  vi.restoreAllMocks();
});

test('lists agents via typed client (sends X-API-Key)', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify([{ agent_id: 'a1', name: 'Triage', autonomy_mode: 'supervised', goal_template: 'g' }]),
      { status: 200, headers: { 'Content-Type': 'application/json' } })
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><AgentsListPage /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText('Triage')).toBeInTheDocument();
  expect((f.mock.calls[0][1] as RequestInit).headers).toMatchObject({ 'X-API-Key': 'k' });
});
