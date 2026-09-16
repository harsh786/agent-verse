import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { MFASettings } from './MFASettings';

type MFAStatus = { enabled: boolean; has_pending_enrollment: boolean; recovery_codes_count: number };

const ENROLL = {
  secret: 'ABCDEFGHIJKLMNOP',
  provisioning_uri: 'otpauth://totp/AgentVerse:me?secret=ABCDEFGHIJKLMNOP',
  qr_code: 'data:image/png;base64,AAAA',
  account_name: 'me',
  issuer: 'AgentVerse',
  algorithm: 'SHA1',
  digits: 6,
  period: 30,
};

function mockFetch(status: MFAStatus, opts: { statusPending?: boolean } = {}) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/auth/mfa/status')) {
      if (opts.statusPending) return new Promise<Response>(() => {}); // never resolves
      return new Response(JSON.stringify(status), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/auth/mfa/enroll') && method === 'POST')
      return new Response(JSON.stringify(ENROLL), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/auth/mfa/verify-enrollment') && method === 'POST')
      return new Response(JSON.stringify({ status: 'enabled', recovery_codes: ['aaaa-1111', 'bbbb-2222'], message: 'ok' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/auth/mfa/disable') && method === 'POST')
      return new Response(JSON.stringify({ status: 'disabled', message: 'ok' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderSettings() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MFASettings />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('MFASettings', () => {
  test('shows a loading spinner while the status query is in flight', () => {
    mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 }, { statusPending: true });
    renderSettings();
    // Neither the enabled nor disabled card has resolved yet.
    expect(screen.queryByText('MFA Enabled')).not.toBeInTheDocument();
    expect(screen.queryByText('MFA Disabled')).not.toBeInTheDocument();
  });

  test('renders the disabled state with an Enable MFA action', async () => {
    mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 });
    renderSettings();
    expect(await screen.findByText('MFA Disabled')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Enable MFA/i })).toBeInTheDocument();
    expect(screen.getByText(/Add an extra layer of security/i)).toBeInTheDocument();
  });

  test('renders the enabled state with the remaining recovery-code count and management actions', async () => {
    mockFetch({ enabled: true, has_pending_enrollment: false, recovery_codes_count: 8 });
    renderSettings();
    expect(await screen.findByText('MFA Enabled')).toBeInTheDocument();
    expect(screen.getByText(/8 recovery codes remaining/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Regenerate codes/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Disable MFA/i })).toBeInTheDocument();
  });

  test('warns when few recovery codes remain', async () => {
    mockFetch({ enabled: true, has_pending_enrollment: false, recovery_codes_count: 2 });
    renderSettings();
    expect(await screen.findByText(/Only 2 recovery codes remaining/i)).toBeInTheDocument();
  });

  test('Enable MFA POSTs to enroll and advances to the QR scan step', async () => {
    const spy = mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) => String(u).includes('/auth/mfa/enroll') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
    expect(await screen.findByText(/Step 1: Scan QR Code/i)).toBeInTheDocument();
    expect(screen.getByAltText('MFA QR Code')).toBeInTheDocument();
    expect(screen.getByText(/Manual entry key/i)).toBeInTheDocument();
  });

  test('completing the verify step POSTs the code and reveals recovery codes', async () => {
    const spy = mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await screen.findByText(/Step 1: Scan QR Code/i);
    await userEvent.click(screen.getByRole('button', { name: /scanned the QR code/i }));
    // Entering 6 digits auto-submits the enrollment verification.
    await userEvent.type(screen.getByLabelText('TOTP code'), '123456');
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/auth/mfa/verify-enrollment') && (i as RequestInit)?.method === 'POST',
        ),
      ).toBe(true),
    );
    expect(await screen.findByText(/Save Your Recovery Codes/i)).toBeInTheDocument();
    expect(screen.getByText('aaaa-1111')).toBeInTheDocument();
  });

  test('the disable flow POSTs the current code to the disable endpoint', async () => {
    const spy = mockFetch({ enabled: true, has_pending_enrollment: false, recovery_codes_count: 8 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Disable MFA/i }));
    await userEvent.type(screen.getByLabelText(/TOTP code to disable MFA/i), '654321');
    // Two "Disable MFA" buttons now exist (status card + step submit); the last
    // one is the confirming submit inside the disable panel.
    const disableButtons = screen.getAllByRole('button', { name: /^Disable MFA$/i });
    await userEvent.click(disableButtons[disableButtons.length - 1]);
    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/auth/mfa/disable') &&
            (i as RequestInit)?.method === 'POST' &&
            String((i as RequestInit)?.body ?? '').includes('654321'),
        ),
      ).toBe(true),
    );
  });
});
