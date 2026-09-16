import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { PrivacySettings } from './PrivacySettings';

function mockFetch(consent = { analytics: false, marketing: true }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/account/consent'))
      return new Response(JSON.stringify(consent), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/account/data-export') && method === 'POST')
      return new Response(JSON.stringify({ status: 'queued' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/account/data-export'))
      return new Response(JSON.stringify({ status: 'idle' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/account/delete-request'))
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
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
    mockFetch({ analytics: false, marketing: true });
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: /Analytics cookies/i })).toHaveAttribute('aria-checked', 'false'),
    );
    expect(screen.getByRole('switch', { name: /Marketing emails/i })).toHaveAttribute('aria-checked', 'true');
  });

  test('toggling a consent switch PUTs the updated preferences', async () => {
    const spy = mockFetch({ analytics: false, marketing: false });
    renderPage();
    const analytics = await screen.findByRole('switch', { name: /Analytics cookies/i });
    await userEvent.click(analytics);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/account/consent') &&
            (i as RequestInit)?.method === 'PUT' &&
            String((i as RequestInit)?.body ?? '').includes('"analytics":true'),
        ),
      ).toBe(true),
    );
  });

  test('Export requests a data export', async () => {
    const spy = mockFetch();
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /request data export/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/account/data-export') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  test('the three-step delete flow POSTs a deletion request and shows confirmation', async () => {
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
            String(u).includes('/account/delete-request') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
    expect(await screen.findByText(/Deletion Requested/i)).toBeInTheDocument();
  });
});
