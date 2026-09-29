import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { PrivacySettings } from './PrivacySettings';

type Jobs = { jobs: Array<Record<string, unknown>> };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function mockFetch(activePurposes: string[] = ['marketing'], jobs: Jobs = { jobs: [] }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/v1/account/')) return json({ detail: 'Not Found' }, 404);
    if (url.endsWith('/compliance/consent') && method === 'GET')
      return json({ active_purposes: activePurposes, consents: [] });
    if (url.endsWith('/compliance/consent') && method === 'POST')
      return json({ consent_id: 'c1', status: 'recorded' });
    if (url.includes('/compliance/consent/') && method === 'DELETE')
      return json({ status: 'revoked', revoked: 1 });
    if (url.endsWith('/compliance/export/start') && method === 'POST')
      return json({ job_id: 'job-1', status: 'pending', poll_url: '/compliance/export/jobs/job-1' });
    if (url.includes('/compliance/export/jobs')) return json(jobs);
    if (url.includes('/tenants/me') && method === 'DELETE') return json({});
    return json({});
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PrivacySettings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('PrivacySettings', () => {
  test('renders the page heading and the GDPR sections', () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('heading', { name: /Privacy & Data/i })).toBeInTheDocument();
    expect(screen.getByText('Download my data')).toBeInTheDocument();
    expect(screen.getByText('Delete account')).toBeInTheDocument();
    expect(screen.getByText(/Danger Zone/i)).toBeInTheDocument();
  });

  test('consent toggles reflect the fetched preferences', async () => {
    mockFetch(['marketing']);
    renderPage();
    // Unloaded consent now renders as NOT granted, so wait for the fetched value.
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: /Marketing emails/i })).toHaveAttribute('aria-checked', 'true'),
    );
    expect(screen.getByRole('switch', { name: /Analytics cookies/i })).toHaveAttribute('aria-checked', 'false');
  });

  test('granting consent POSTs the purpose to /compliance/consent', async () => {
    const spy = mockFetch([]);
    renderPage();
    const analytics = await screen.findByRole('switch', { name: /Analytics cookies/i });
    await waitFor(() => expect(analytics).not.toBeDisabled());
    await userEvent.click(analytics);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).endsWith('/compliance/consent') &&
            (i as RequestInit)?.method === 'POST' &&
            String((i as RequestInit)?.body ?? '').includes('"purpose":"analytics"'),
        ),
      ).toBe(true),
    );
  });

  test('revoking consent DELETEs /compliance/consent/{purpose}', async () => {
    const spy = mockFetch(['marketing']);
    renderPage();
    const marketing = await screen.findByRole('switch', { name: /Marketing emails/i });
    await waitFor(() => expect(marketing).toHaveAttribute('aria-checked', 'true'));
    await userEvent.click(marketing);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).endsWith('/compliance/consent/marketing') &&
            (i as RequestInit)?.method === 'DELETE',
        ),
      ).toBe(true),
    );
  });

  test('a failed consent update is reported, not silently dropped', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.endsWith('/compliance/consent') && method === 'GET')
        return json({ active_purposes: [], consents: [] });
      if (url.endsWith('/compliance/consent')) return json({ detail: 'Consent could not be recorded; retry' }, 503);
      return json({ jobs: [] });
    });
    renderPage();
    const analytics = await screen.findByRole('switch', { name: /Analytics cookies/i });
    await waitFor(() => expect(analytics).not.toBeDisabled());
    await userEvent.click(analytics);
    expect(await screen.findByText(/could not be saved/i)).toBeInTheDocument();
  });

  test('Export requests a data export', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /request data export/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).endsWith('/compliance/export/start') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('the three-step delete flow schedules the real erasure (DELETE /tenants/me)', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /request account deletion/i }));

    expect(await screen.findByRole('heading', { name: /Delete Account\?/i })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^Continue$/i }));
    await userEvent.click(screen.getByRole('button', { name: /I understand, continue/i }));
    await userEvent.click(screen.getByRole('button', { name: /Request Deletion/i }));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/tenants/me') && (i as RequestInit)?.method === 'DELETE',
        ),
      ).toBe(true),
    );
    expect(await screen.findByText(/Deletion Requested/i)).toBeInTheDocument();
  });

  test('shows the latest real export job and its download link', async () => {
    mockFetch([], {
      jobs: [{ job_id: 'job-9', status: 'complete', created_at: '2026-01-01T00:00:00Z', completed_at: '2026-01-01T00:01:00Z', download_url: '/enterprise/compliance/export/job-9/download', error: null }],
    });
    renderPage();
    expect(await screen.findByRole('button', { name: /Download archive/i })).toBeInTheDocument();
  });

  test('a failed export job is shown as failed', async () => {
    mockFetch([], {
      jobs: [{ job_id: 'job-8', status: 'failed', created_at: null, completed_at: null, download_url: null, error: 'could not be enqueued' }],
    });
    renderPage();
    expect(await screen.findByText(/last export failed/i)).toBeInTheDocument();
  });

  test('no call targets the non-existent /v1/account/* routes', async () => {
    const spy = mockFetch();
    renderPage();
    await screen.findByRole('switch', { name: /Analytics cookies/i });
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls.some(([u]) => String(u).includes('/v1/account/'))).toBe(false);
  });

  test('a failed consent load is not rendered as granted consent', async () => {
    // Regression: the query's catch fabricated {analytics: true}.
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404, headers: { 'Content-Type': 'application/json' } }));
    renderPage();
    expect(await screen.findByText(/consent settings could not be loaded/i)).toHaveAttribute('role', 'alert');
    expect(screen.getByRole('switch', { name: /Analytics cookies/i })).toHaveAttribute('aria-checked', 'false');
  });
});
