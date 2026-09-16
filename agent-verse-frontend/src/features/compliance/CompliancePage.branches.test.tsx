import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CompliancePage } from './CompliancePage';

interface MockOpts {
  compliant?: boolean;
  holds?: unknown[];
  holdsError?: boolean;
  contracts?: unknown[];
}

function mockFetch(opts: MockOpts = {}) {
  const {
    compliant = true,
    holds = [{ id: 'h1', reason: 'litigation hold', expires_at: null, created_by: 'admin' }],
    holdsError = false,
    contracts = [],
  } = opts;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init as RequestInit | undefined)?.method ?? 'GET';
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

    if (url.includes('/governance/legal-holds')) {
      if (holdsError) return new Response('nope', { status: 500 });
      return json(holds);
    }
    if (url.includes('/governance/legal-hold') && method === 'POST')
      return json({ status: 'active', tenant_id: 't', reason: 'x' });
    if (url.includes('/compliance/export/start') && method === 'POST')
      return json({ job_id: 'j1', status: 'pending', poll_url: '/compliance/export/jobs/j1' });
    if (url.includes('/compliance/export/jobs/'))
      return json({ job_id: 'j1', status: 'complete', completed_at: '2026-01-01T00:00:00Z', download_url: 'https://example.com/export.zip', error: null });
    if (url.includes('/compliance/consent') && method === 'POST')
      return json({ purpose: 'analytics', legal_basis: 'consent', status: 'recorded' });
    if (url.includes('/compliance/consent') && method === 'DELETE')
      return json({ purpose: 'analytics', status: 'revoked' });
    if (url.includes('/enterprise/compliance/') && url.includes('/check') && method === 'POST')
      return json({ framework: 'gdpr', compliant, checks: [{ check: 'audit_trail', passed: compliant }], tenant_id: 't' });
    if (url.includes('/enterprise/compliance/'))
      return json({ framework: 'gdpr', compliant, checks: [{ check: 'audit_trail', passed: compliant }], tenant_id: 't' });
    if (url.includes('/enterprise/contracts/') && url.includes('/sign') && method === 'POST')
      return json({ contract_type: 'dpa', status: 'signed' });
    if (url.includes('/enterprise/contracts'))
      return json(contracts);
    return json({});
  });
}

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
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'enterprise', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('CompliancePage — branches', () => {
  test('non-compliant frameworks render the Non-compliant badge', async () => {
    mockFetch({ compliant: false });
    renderPage();
    await waitFor(() => expect(screen.getAllByText('Non-compliant').length).toBe(3));
  });

  test('compliant frameworks render the Compliant badge and checks', async () => {
    mockFetch({ compliant: true });
    renderPage();
    await waitFor(() => expect(screen.getAllByText('Compliant').length).toBe(3));
    expect(screen.getAllByText('audit_trail').length).toBe(3);
  });

  test('Re-run check fires a POST to the framework check endpoint', async () => {
    const spy = mockFetch();
    renderPage();
    const buttons = await screen.findAllByRole('button', { name: /Re-run check/i });
    await userEvent.click(buttons[0]);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/enterprise/compliance/gdpr/check') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  test('Legal Holds empty state renders when there are no holds', async () => {
    mockFetch({ holds: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /legal holds/i }));
    expect(await screen.findByText(/No active legal holds/i)).toBeInTheDocument();
  });

  test('Legal Holds error state renders on a failed load', async () => {
    mockFetch({ holdsError: true });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /legal holds/i }));
    expect(await screen.findByText(/Failed to load legal holds/i)).toBeInTheDocument();
  });

  test('Placing a legal hold POSTs to /governance/legal-hold', async () => {
    const spy = mockFetch({ holds: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /legal holds/i }));
    await userEvent.click(await screen.findByTestId('place-hold-btn'));
    const reason = await screen.findByPlaceholderText(/Describe the legal/i);
    await userEvent.type(reason, 'Pending audit');
    const submits = screen.getAllByRole('button', { name: /^Place Hold$/i });
    await userEvent.click(submits[submits.length - 1]);
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/governance/legal-hold') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  test('Data Export shows the Download Archive link when the job completes', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /data export/i }));
    await userEvent.click(await screen.findByTestId('start-export-btn'));
    expect(await screen.findByRole('link', { name: /Download Archive/i })).toHaveAttribute(
      'href', 'https://example.com/export.zip',
    );
  });

  test('Contracts empty state renders when no contracts exist', async () => {
    mockFetch({ contracts: [] });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /contracts/i }));
    expect(await screen.findByText(/No contracts available/i)).toBeInTheDocument();
  });

  test('Signing a pending contract POSTs to the sign endpoint', async () => {
    const spy = mockFetch({
      contracts: [{ contract_id: 'ct1', contract_type: 'dpa', status: 'pending_signature' }],
    });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /contracts/i }));
    await userEvent.click(await screen.findByRole('button', { name: /^Sign$/i }));
    const dialogTitle = await screen.findByText(/^Sign: /i);
    expect(dialogTitle).toBeInTheDocument();
    await userEvent.type(screen.getByPlaceholderText(/Jane Smith/i), 'Jane Smith');
    await userEvent.type(screen.getByPlaceholderText(/jane@company.com/i), 'jane@company.com');
    await userEvent.click(screen.getByRole('button', { name: /Sign Contract/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/enterprise/contracts/dpa/sign') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  test('Consent tab records consent via POST', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /consent/i }));
    await userEvent.click(await screen.findByRole('button', { name: /Record consent/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/compliance/consent') && (i as RequestInit)?.method === 'POST'),
      ).toBe(true),
    );
  });

  test('Consent tab revokes consent via DELETE', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /consent/i }));
    await userEvent.click(await screen.findByRole('button', { name: /Revoke consent/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) =>
        String(u).includes('/compliance/consent/analytics') && (i as RequestInit)?.method === 'DELETE'),
      ).toBe(true),
    );
  });

  test('changing consent purpose updates the preview', async () => {
    mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /consent/i }));
    const section = await screen.findByTestId('consent-section');
    const purposeSelect = within(section).getAllByRole('combobox')[0];
    await userEvent.selectOptions(purposeSelect, 'marketing');
    // Scope to the "Selected" preview block (the label also appears as an <option>).
    const previewBlock = within(section).getByText('Selected').closest('div') as HTMLElement;
    expect(within(previewBlock).getByText('Marketing')).toBeInTheDocument();
  });
});
