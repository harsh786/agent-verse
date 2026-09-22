import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
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

function mockFetch(
  status: MFAStatus,
  opts: {
    statusPending?: boolean;
    enrollFails?: boolean;
    verifyFails?: boolean;
    disableFails?: boolean;
    regenFails?: boolean;
    noQrCode?: boolean;
  } = {},
) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/auth/mfa/status')) {
      if (opts.statusPending) return new Promise<Response>(() => {}); // never resolves
      return new Response(JSON.stringify(status), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/auth/mfa/enroll') && method === 'POST') {
      if (opts.enrollFails) return new Response(JSON.stringify({ message: 'enroll failed' }), { status: 500, headers: { 'Content-Type': 'application/json' } });
      const body = opts.noQrCode ? { ...ENROLL, qr_code: null } : ENROLL;
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/auth/mfa/verify-enrollment') && method === 'POST') {
      if (opts.verifyFails) return new Response(JSON.stringify({ message: 'invalid code' }), { status: 400, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({ status: 'enabled', recovery_codes: ['aaaa-1111', 'bbbb-2222'], message: 'ok' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/auth/mfa/disable') && method === 'POST') {
      if (opts.disableFails) return new Response(JSON.stringify({ message: 'invalid code' }), { status: 400, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({ status: 'disabled', message: 'ok' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/auth/mfa/regenerate') && method === 'POST') {
      if (opts.regenFails) return new Response(JSON.stringify({ message: 'invalid code' }), { status: 400, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({ recovery_codes: ['cccc-3333', 'dddd-4444'], message: 'ok' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
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

  test('singular grammar is used when exactly one recovery code remains', async () => {
    mockFetch({ enabled: true, has_pending_enrollment: false, recovery_codes_count: 1 });
    renderSettings();
    expect(await screen.findByText('MFA Enabled')).toBeInTheDocument();
    expect(
      screen.getByText((_, node) => node?.textContent === '1 recovery code remaining'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        (_, node) =>
          node?.tagName === 'P' &&
          !!node.textContent?.includes('Only 1 recovery code remaining. Regenerate them before'),
      ),
    ).toBeInTheDocument();
  });

  test('enroll failure toasts an error and stays on the idle step', async () => {
    mockFetch(
      { enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 },
      { enrollFails: true },
    );
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await waitFor(() =>
      expect(useToastStore.getState().toasts.some((t) => t.kind === 'error')).toBe(true),
    );
    expect(screen.queryByText(/Step 1: Scan QR Code/i)).not.toBeInTheDocument();
  });

  test('enrollment without a QR code falls back to the manual-entry message', async () => {
    mockFetch(
      { enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 },
      { noQrCode: true },
    );
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    expect(await screen.findByText(/QR code unavailable/i)).toBeInTheDocument();
    expect(screen.queryByAltText('MFA QR Code')).not.toBeInTheDocument();
  });

  test('toggling and copying the manual entry secret', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await screen.findByText(/Step 1: Scan QR Code/i);

    // Secret is hidden by default (blurred) and the copy button is not shown yet.
    expect(screen.queryByRole('button', { name: 'Copy secret' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Show secret' }));
    await userEvent.click(screen.getByRole('button', { name: 'Copy secret' }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(ENROLL.secret);
    expect(
      useToastStore.getState().toasts.some((t) => t.kind === 'success' && t.message === 'Secret copied'),
    ).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Hide secret' }));
    expect(screen.queryByRole('button', { name: 'Copy secret' })).not.toBeInTheDocument();
  });

  test('the Back button in the verify step returns to the QR scan step', async () => {
    mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await userEvent.click(await screen.findByRole('button', { name: /scanned the QR code/i }));
    expect(await screen.findByText(/Step 2: Verify your code/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Back/i }));
    expect(await screen.findByText(/Step 1: Scan QR Code/i)).toBeInTheDocument();
  });

  test('an invalid verification code toasts an error, clears the field, and refocuses input', async () => {
    mockFetch(
      { enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 },
      { verifyFails: true },
    );
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await userEvent.click(await screen.findByRole('button', { name: /scanned the QR code/i }));
    const input = screen.getByLabelText('TOTP code');
    await userEvent.type(input, '000000');
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'error' && /Invalid code/i.test(t.message)),
      ).toBe(true),
    );
    expect(input).toHaveValue('');
    expect(input).toHaveFocus();
  });

  test('non-digit characters are stripped from the TOTP input and it is capped at 6 digits', async () => {
    mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await userEvent.click(await screen.findByRole('button', { name: /scanned the QR code/i }));
    const input = screen.getByLabelText('TOTP code');
    await userEvent.type(input, 'ab12cd34ef56gh');
    expect(input).toHaveValue('123456');
  });

  test('the Verify & Enable button is disabled until 6 digits are entered', async () => {
    mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await userEvent.click(await screen.findByRole('button', { name: /scanned the QR code/i }));
    const submit = screen.getByRole('button', { name: /Verify & Enable/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText('TOTP code'), '12345');
    expect(submit).toBeDisabled();
  });

  test('copying a single recovery code and copying all codes', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
    mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await userEvent.click(await screen.findByRole('button', { name: /scanned the QR code/i }));
    await userEvent.type(screen.getByLabelText('TOTP code'), '123456');
    await screen.findByText('aaaa-1111');

    await userEvent.click(screen.getByText('aaaa-1111'));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('aaaa-1111');

    await userEvent.click(screen.getByRole('button', { name: /Copy all codes/i }));
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('aaaa-1111\nbbbb-2222');
    expect(
      useToastStore
        .getState()
        .toasts.some((t) => t.kind === 'success' && t.message === 'All recovery codes copied!'),
    ).toBe(true);
  });

  test('finishing the recovery-codes step returns to idle and refetches status', async () => {
    mockFetch({ enabled: false, has_pending_enrollment: false, recovery_codes_count: 0 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Enable MFA/i }));
    await userEvent.click(await screen.findByRole('button', { name: /scanned the QR code/i }));
    await userEvent.type(screen.getByLabelText('TOTP code'), '123456');
    await screen.findByText(/Save Your Recovery Codes/i);

    await userEvent.click(screen.getByRole('button', { name: /Done/i }));
    expect(screen.queryByText(/Save Your Recovery Codes/i)).not.toBeInTheDocument();
  });

  test('cancelling the disable flow returns to idle without submitting', async () => {
    mockFetch({ enabled: true, has_pending_enrollment: false, recovery_codes_count: 8 });
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Disable MFA/i }));
    await userEvent.type(screen.getByLabelText(/TOTP code to disable MFA/i), '111111');
    await userEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(screen.queryByLabelText(/TOTP code to disable MFA/i)).not.toBeInTheDocument();
  });

  test('an invalid disable code toasts an error and keeps MFA enabled', async () => {
    mockFetch(
      { enabled: true, has_pending_enrollment: false, recovery_codes_count: 8 },
      { disableFails: true },
    );
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Disable MFA/i }));
    await userEvent.type(screen.getByLabelText(/TOTP code to disable MFA/i), '222222');
    const disableButtons = screen.getAllByRole('button', { name: /^Disable MFA$/i });
    await userEvent.click(disableButtons[disableButtons.length - 1]);
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message === 'Invalid code'),
      ).toBe(true),
    );
    expect(screen.getByText('MFA Enabled')).toBeInTheDocument();
  });

  test('the regenerate flow POSTs the code, shows new recovery codes, and cancel works', async () => {
    const spy = mockFetch({ enabled: true, has_pending_enrollment: false, recovery_codes_count: 8 });
    renderSettings();

    // Cancel path first.
    await userEvent.click(await screen.findByRole('button', { name: /Regenerate codes/i }));
    await userEvent.type(screen.getByLabelText(/TOTP code to regenerate recovery codes/i), '333');
    await userEvent.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(screen.queryByLabelText(/TOTP code to regenerate recovery codes/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Regenerate codes/i }));
    await userEvent.type(screen.getByLabelText(/TOTP code to regenerate recovery codes/i), '444444');
    await userEvent.click(screen.getByRole('button', { name: /^Regenerate$/i }));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(
          ([u, i]) =>
            String(u).includes('/auth/mfa/regenerate') &&
            (i as RequestInit)?.method === 'POST' &&
            String((i as RequestInit)?.body ?? '').includes('444444'),
        ),
      ).toBe(true),
    );
    expect(await screen.findByText('cccc-3333')).toBeInTheDocument();
  });

  test('an invalid regenerate code toasts an error', async () => {
    mockFetch(
      { enabled: true, has_pending_enrollment: false, recovery_codes_count: 8 },
      { regenFails: true },
    );
    renderSettings();
    await userEvent.click(await screen.findByRole('button', { name: /Regenerate codes/i }));
    await userEvent.type(screen.getByLabelText(/TOTP code to regenerate recovery codes/i), '555555');
    await userEvent.click(screen.getByRole('button', { name: /^Regenerate$/i }));
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message === 'Invalid code'),
      ).toBe(true),
    );
  });
});
