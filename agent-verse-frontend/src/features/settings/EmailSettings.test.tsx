import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import type { TenantEmailSettings } from '@/lib/api/client';
import { EmailSettings } from './EmailSettings';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const PLATFORM: TenantEmailSettings = {
  tenant_id: 't',
  recipient_allowlist: [],
  smtp: null,
  relay: 'platform',
  updated_at: null,
};

const CONFIGURED: TenantEmailSettings = {
  ...PLATFORM,
  recipient_allowlist: ['partner.io'],
  relay: 'tenant',
  smtp: {
    host: 'smtp.tenant.test',
    port: 587,
    tls_mode: 'starttls',
    username: 'mailer',
    from_address: 'bot@tenant.test',
    secret_set: true,
    secret_masked: '********',
  },
};

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <EmailSettings />
    </QueryClientProvider>,
  );
}

type Handler = (url: string, init: RequestInit | undefined) => Response | undefined;

function mockFetch(handler: Handler, fallback: TenantEmailSettings) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    return handler(url, init) ?? json(fallback);
  });
}

function bodyOf(spy: ReturnType<typeof mockFetch>, pathEnd: string, method: string) {
  const call = spy.mock.calls.find(
    ([u, init]) => String(u).endsWith(pathEnd) && (init?.method ?? 'GET') === method,
  );
  return call ? JSON.parse(String(call[1]?.body)) : undefined;
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('EmailSettings', () => {
  test('shows the platform relay when no SMTP sender is configured', async () => {
    mockFetch(() => undefined, PLATFORM);
    renderIt();
    expect(await screen.findByTestId('email-relay')).toHaveTextContent(/platform relay/i);
    expect(screen.queryByRole('button', { name: /Remove/i })).not.toBeInTheDocument();
  });

  test('saves the allowlist as entries', async () => {
    const spy = mockFetch(
      (url, init) =>
        url.endsWith('/tenants/me/email/allowlist') && init?.method === 'PUT'
          ? json({ ...PLATFORM, recipient_allowlist: ['example.com', 'boss@corp.io'] })
          : undefined,
      PLATFORM,
    );
    renderIt();
    const box = await screen.findByLabelText('Recipient allowlist');
    await userEvent.type(box, 'example.com{enter}boss@corp.io');
    await userEvent.click(screen.getByRole('button', { name: /Save allowlist/i }));
    await waitFor(() =>
      expect(bodyOf(spy, '/tenants/me/email/allowlist', 'PUT')).toEqual({
        entries: ['example.com', 'boss@corp.io'],
      }),
    );
    expect(await screen.findByText('2 entries')).toBeInTheDocument();
  });

  test('the stored secret is masked, never prefilled, and omitted when left blank', async () => {
    const spy = mockFetch(
      (url, init) =>
        url.endsWith('/tenants/me/email/smtp') && init?.method === 'PUT' ? json(CONFIGURED) : undefined,
      CONFIGURED,
    );
    renderIt();
    expect(await screen.findByTestId('email-relay')).toHaveTextContent(/your SMTP server/i);
    const secret = screen.getByLabelText('Password or API key') as HTMLInputElement;
    expect(secret.type).toBe('password');
    expect(secret.value).toBe('');
    expect(secret.placeholder).toMatch(/\*{8}.*leave blank to keep/);
    await userEvent.click(screen.getByRole('button', { name: /Save SMTP sender/i }));
    await waitFor(() => expect(bodyOf(spy, '/tenants/me/email/smtp', 'PUT')).toBeDefined());
    const sent = bodyOf(spy, '/tenants/me/email/smtp', 'PUT');
    expect(sent).toEqual({
      host: 'smtp.tenant.test',
      port: 587,
      tls_mode: 'starttls',
      username: 'mailer',
      from_address: 'bot@tenant.test',
    });
    expect(sent).not.toHaveProperty('secret');
  });

  test('a typed secret is sent once and cleared after saving', async () => {
    const typed = `pw-${Math.random().toString(36).slice(2)}`;
    const spy = mockFetch(
      (url, init) =>
        url.endsWith('/tenants/me/email/smtp') && init?.method === 'PUT' ? json(CONFIGURED) : undefined,
      PLATFORM,
    );
    renderIt();
    await userEvent.type(await screen.findByLabelText('SMTP host'), 'smtp.tenant.test');
    await userEvent.type(screen.getByLabelText('Username'), 'mailer');
    await userEvent.type(screen.getByLabelText('Password or API key'), typed);
    await userEvent.type(screen.getByLabelText('From address'), 'bot@tenant.test');
    await userEvent.click(screen.getByRole('button', { name: /Save SMTP sender/i }));
    await waitFor(() => expect(bodyOf(spy, '/tenants/me/email/smtp', 'PUT')?.secret).toBe(typed));
    await waitFor(() =>
      expect((screen.getByLabelText('Password or API key') as HTMLInputElement).value).toBe(''),
    );
    expect(screen.getByTestId('email-relay')).toHaveTextContent(/your SMTP server/i);
  });

  test('Test connection posts the form and shows the failing stage', async () => {
    const spy = mockFetch(
      (url) =>
        url.endsWith('/tenants/me/email/smtp/test')
          ? json({ ok: false, stage: 'auth', message: 'authentication failed', smtp_code: 535, tested: 'candidate' })
          : undefined,
      CONFIGURED,
    );
    renderIt();
    await userEvent.click(await screen.findByRole('button', { name: /Test connection/i }));
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Failed at auth: authentication failed (SMTP 535)',
    );
    expect(bodyOf(spy, '/tenants/me/email/smtp/test', 'POST')).toEqual({
      config: {
        host: 'smtp.tenant.test',
        port: 587,
        tls_mode: 'starttls',
        username: 'mailer',
        from_address: 'bot@tenant.test',
      },
    });
  });

  test('changing the encryption mode suggests its port', async () => {
    mockFetch(() => undefined, PLATFORM);
    renderIt();
    await userEvent.selectOptions(await screen.findByLabelText('Encryption'), 'tls');
    expect((screen.getByLabelText('Port') as HTMLInputElement).value).toBe('465');
  });

  test('removing the sender falls back to the platform relay', async () => {
    const spy = mockFetch(
      (url, init) =>
        url.endsWith('/tenants/me/email/smtp') && init?.method === 'DELETE' ? json(PLATFORM) : undefined,
      CONFIGURED,
    );
    renderIt();
    await userEvent.click(await screen.findByRole('button', { name: /Remove/i }));
    await waitFor(() => expect(screen.getByTestId('email-relay')).toHaveTextContent(/platform relay/i));
    expect(spy.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(true);
  });

  test('non-admins get an explanation instead of the form', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      json({ detail: 'Insufficient permissions' }, 403),
    );
    renderIt();
    expect(await screen.findByRole('alert')).toHaveTextContent(/Only workspace admins/i);
    expect(screen.queryByLabelText('SMTP host')).not.toBeInTheDocument();
  });
});
