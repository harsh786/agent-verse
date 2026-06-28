import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import AuditExplorerPage from './AuditExplorerPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><AuditExplorerPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

const SAMPLE = [
  { event_id: 'e1', goal_id: 'g1', tool_name: 'jira.delete', action_level: 'deny', outcome: 'denied', note: '' },
  { event_id: 'e2', goal_id: 'g2', tool_name: 'github.read', action_level: 'allow', outcome: 'success', note: '' },
];

test('renders typed audit rows from auditApi', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(SAMPLE), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderPage();
  expect(await screen.findByText('jira.delete')).toBeInTheDocument();
  expect(screen.getByText('github.read')).toBeInTheDocument();
});

test('tool filter is forwarded as a query param', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(SAMPLE), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderPage();
  await screen.findByText('jira.delete');
  await userEvent.type(screen.getByLabelText(/tool name/i), 'jira.delete');
  await userEvent.click(screen.getByRole('button', { name: /apply filters/i }));
  await waitFor(() =>
    expect(f.mock.calls.some(([u]) => String(u).includes('tool_name=jira.delete'))).toBe(true),
  );
});

test('export JSON button is present once rows load', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(SAMPLE), { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
  renderPage();
  await screen.findByText('jira.delete');
  expect(screen.getByRole('button', { name: /export json/i })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /export csv/i })).toBeInTheDocument();
});
