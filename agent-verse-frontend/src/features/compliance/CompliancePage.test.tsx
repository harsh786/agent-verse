import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CompliancePage } from './CompliancePage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><CompliancePage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});

test('lists legal holds', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/governance/legal-holds'))
      return new Response(JSON.stringify([{ id: 'h1', reason: 'litigation', expires_at: null, created_by: 'admin' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  expect(await screen.findByText(/litigation/i)).toBeInTheDocument();
});

test('starting a GDPR export begins polling the job', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/compliance/export/start') && (init as RequestInit)?.method === 'POST')
      return new Response(JSON.stringify({ job_id: 'j1', status: 'pending', poll_url: '/compliance/export/jobs/j1' }), { status: 200 });
    if (url.includes('/compliance/export/jobs/j1'))
      return new Response(JSON.stringify({ job_id: 'j1', status: 'complete', completed_at: '2026-06-28T00:00:00Z', download_url: 'https://x/export.zip', error: null }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  renderPage();
  await userEvent.click(await screen.findByRole('button', { name: /start gdpr export/i }));
  await waitFor(() =>
    expect(f.mock.calls.some(([u]) => String(u).includes('/compliance/export/jobs/j1'))).toBe(true),
  );
  expect(await screen.findByRole('link', { name: /download/i })).toBeInTheDocument();
});
