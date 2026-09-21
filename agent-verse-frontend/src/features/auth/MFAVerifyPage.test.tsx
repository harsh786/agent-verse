/**
 * Tests for MFAVerifyPage — the post-login two-factor verification screen.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import MFAVerifyPage from './MFAVerifyPage';

function mockFetch(status = 200, payload: unknown = { status: 'verified', method: 'totp', remaining_recovery_codes: 8 }) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/auth/mfa/verify') && method === 'POST')
      return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/mfa']}>
        <MFAVerifyPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function verifyBody(spy: ReturnType<typeof mockFetch>): unknown {
  const call = spy.mock.calls.find(([u, i]) => String(u).includes('/auth/mfa/verify') && (i as RequestInit)?.method === 'POST');
  const init = call?.[1] as RequestInit | undefined;
  return init?.body ? JSON.parse(init.body as string) : undefined;
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true, mfaRequired: true, mfaToken: 'pending' });
  useToastStore.setState({ toasts: [] });
});
afterEach(() => vi.restoreAllMocks());

describe('MFAVerifyPage', () => {
  test('renders the TOTP prompt by default', () => {
    mockFetch();
    renderPage();
    expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
    expect(screen.getByText(/Enter the 6-digit code from your authenticator app/i)).toBeInTheDocument();
    expect(screen.getByLabelText('TOTP verification code')).toBeInTheDocument();
  });

  test('submitting the form with a blank code does not trigger verification', async () => {
    const spy = mockFetch();
    renderPage();
    const form = screen.getByLabelText('TOTP verification code').closest('form');
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);

    await new Promise((r) => setTimeout(r, 50));
    expect(spy.mock.calls.some(([u]) => String(u).includes('/auth/mfa/verify'))).toBe(false);
  });

  test('the Verify button is disabled until 6 digits are entered', () => {
    mockFetch();
    renderPage();
    const submit = screen.getByRole('button', { name: /Verify/i });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText('TOTP verification code'), { target: { value: '123456' } });
    expect(submit).toBeEnabled();
  });

  test('entering 6 digits auto-submits and POSTs the code to /auth/mfa/verify', async () => {
    const spy = mockFetch();
    renderPage();
    fireEvent.change(screen.getByLabelText('TOTP verification code'), { target: { value: '123456' } });
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/auth/mfa/verify') && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
    expect(verifyBody(spy)).toEqual({ code: '123456' });
  });

  test('strips non-digits and does not auto-submit before 6 digits', async () => {
    const spy = mockFetch();
    renderPage();
    fireEvent.change(screen.getByLabelText('TOTP verification code'), { target: { value: '12ab3' } });
    // Only 3 digits survived the filter → no verify request yet.
    expect(screen.getByLabelText('TOTP verification code')).toHaveValue('123');
    await new Promise((r) => setTimeout(r, 200));
    expect(spy.mock.calls.some(([u]) => String(u).includes('/auth/mfa/verify'))).toBe(false);
  });

  test('switching to recovery mode verifies an 11-char recovery code on submit', async () => {
    const spy = mockFetch();
    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /Use a recovery code instead/i }));
    expect(screen.getByText(/Enter one of your recovery codes/i)).toBeInTheDocument();
    const input = screen.getByLabelText('Recovery code');
    fireEvent.change(input, { target: { value: 'aaaaa-bbbbb' } });
    fireEvent.click(screen.getByRole('button', { name: /Verify/i }));
    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => String(u).includes('/auth/mfa/verify') && (i as RequestInit)?.method === 'POST')).toBe(true),
    );
    // Recovery codes are upper-cased by the input handler.
    expect(verifyBody(spy)).toEqual({ code: 'AAAAA-BBBBB' });
  });

  test('clears the code and stays on the page when verification fails', async () => {
    mockFetch(400, { detail: 'bad code' });
    renderPage();
    const input = screen.getByLabelText('TOTP verification code');
    fireEvent.change(input, { target: { value: '000000' } });
    await waitFor(() => expect(input).toHaveValue(''));
    expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
  });

  test('shows an error toast and refocuses the input when the code is rejected', async () => {
    mockFetch(400, { detail: 'bad code' });
    renderPage();
    const input = screen.getByLabelText('TOTP verification code');
    fireEvent.change(input, { target: { value: '000000' } });

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'error' && t.message === 'Invalid code. Please try again.')
      ).toBe(true);
    });
    expect(input).toHaveFocus();
  });

  test('shows a success toast and navigates to /goals on successful verification', async () => {
    mockFetch(200, { status: 'verified', method: 'totp', remaining_recovery_codes: 8 });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/mfa']}>
          <Routes>
            <Route path="/mfa" element={<MFAVerifyPage />} />
            <Route path="/goals" element={<div>Goals Page</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );

    fireEvent.change(screen.getByLabelText('TOTP verification code'), { target: { value: '123456' } });

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'success' && t.message === 'Verified! Welcome back.')
      ).toBe(true);
    });
    expect(await screen.findByText('Goals Page')).toBeInTheDocument();
    expect(useAuthStore.getState().mfaRequired).toBe(false);
    expect(useAuthStore.getState().mfaToken).toBeNull();
  });

  test('warns when recovery codes are running low after a successful recovery-code verification', async () => {
    mockFetch(200, { status: 'verified', method: 'recovery', remaining_recovery_codes: 2 });
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /Use a recovery code instead/i }));
    fireEvent.change(screen.getByLabelText('Recovery code'), { target: { value: 'aaaaa-bbbbb' } });
    fireEvent.click(screen.getByRole('button', { name: /Verify/i }));

    await waitFor(() => {
      expect(
        useToastStore
          .getState()
          .toasts.some(
            (t) => t.kind === 'warning' && t.message === 'Only 2 recovery codes remaining. Regenerate soon.'
          )
      ).toBe(true);
    });
  });

  test('does not warn about recovery codes when remaining count is comfortably above the threshold', async () => {
    mockFetch(200, { status: 'verified', method: 'totp', remaining_recovery_codes: 8 });
    renderPage();

    fireEvent.change(screen.getByLabelText('TOTP verification code'), { target: { value: '123456' } });

    await waitFor(() => {
      expect(
        useToastStore.getState().toasts.some((t) => t.kind === 'success')
      ).toBe(true);
    });
    expect(useToastStore.getState().toasts.some((t) => t.kind === 'warning')).toBe(false);
  });
});
