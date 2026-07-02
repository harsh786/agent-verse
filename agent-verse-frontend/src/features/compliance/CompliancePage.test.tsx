import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
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

function mockFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    if (url.includes('/governance/legal-holds'))
      return new Response(JSON.stringify([{ id: 'h1', reason: 'litigation hold', expires_at: null, created_by: 'admin' }]),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/compliance/export/start') && method === 'POST')
      return new Response(JSON.stringify({ job_id: 'j1', status: 'pending', poll_url: '/compliance/export/jobs/j1' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/compliance/export/jobs/j1'))
      return new Response(JSON.stringify({ job_id: 'j1', status: 'complete', completed_at: '2026-01-01T00:00:00Z', download_url: 'https://example.com/export.zip', error: null }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/enterprise/compliance/'))
      return new Response(JSON.stringify({ framework: 'gdpr', compliant: true, checks: [{ check: 'audit_trail', passed: true }], tenant_id: 't' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/enterprise/contracts'))
      return new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'enterprise', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CompliancePage', () => {
  test('renders Compliance heading', async () => {
    mockFetch();
    renderPage();
    expect(await screen.findByRole('heading', { name: /compliance/i })).toBeInTheDocument();
  });

  test('tabs are present (Frameworks, Legal Holds, etc.)', async () => {
    mockFetch();
    renderPage();
    // Tabs are regular buttons (not role="tab")
    await waitFor(() => expect(screen.getByRole('button', { name: /frameworks/i })).toBeInTheDocument());
  });

  test('shows legal holds when Legal Holds tab selected', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /legal holds/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /legal holds/i }));
    expect(await screen.findByTestId('legal-holds-section')).toBeInTheDocument();
    expect(await screen.findByText(/litigation hold/i)).toBeInTheDocument();
  });

  test('GDPR export button triggers POST to export/start', async () => {
    const spy = mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByRole('button', { name: /data export/i })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /data export/i }));
    const exportBtn = await screen.findByTestId('start-export-btn');
    await userEvent.click(exportBtn);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/compliance/export/start') && (i as RequestInit)?.method === 'POST'
      )).toBe(true)
    );
  });

  test('framework status section loads on Frameworks tab', async () => {
    mockFetch();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('framework-status')).toBeInTheDocument());
  });
});
