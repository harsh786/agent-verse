import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import React, { type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { SlackSetup } from './SlackSetup';

// Framer's AnimatePresence gates the wizard steps; collapse the animation layer
// so step transitions are synchronous in jsdom.
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  const makeStub = (tag: string) =>
    ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: new Proxy(actual.motion as unknown as Record<string, unknown>, {
      get: (target, key: string) => (key in target ? target[key] : makeStub(key)),
    }),
  };
});

function mockFetch(ok = true) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/gateway/config') && method === 'PUT') {
      return ok
        ? new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })
        : new Response(JSON.stringify({ detail: 'bad token' }), { status: 400, headers: { 'Content-Type': 'application/json' } });
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderWizard(onConnected?: () => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><SlackSetup orgId="org-1" onConnected={onConnected} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('SlackSetup', () => {
  test('step 1 shows the setup instructions and a link to the Slack API', () => {
    mockFetch();
    renderWizard();
    expect(screen.getByRole('heading', { name: /Connect Slack Workspace/i })).toBeInTheDocument();
    expect(screen.getByText(/Bot Token Scopes/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open Slack API/i })).toHaveAttribute(
      'href',
      'https://api.slack.com/apps',
    );
  });

  test('advancing to step 2 reveals the credential fields', async () => {
    mockFetch();
    renderWizard();
    await userEvent.click(screen.getByRole('button', { name: /I have my credentials/i }));
    expect(screen.getByLabelText('Bot Token')).toBeInTheDocument();
    expect(screen.getByLabelText('Signing Secret')).toBeInTheDocument();
  });

  test('the Connect button stays disabled until both credentials are entered', async () => {
    mockFetch();
    renderWizard();
    await userEvent.click(screen.getByRole('button', { name: /I have my credentials/i }));
    // Re-query the button each time — the motion layer re-renders (replacing DOM
    // nodes) on every state change, so a cached reference would go stale.
    const connectBtn = () => screen.getByRole('button', { name: /Connect Slack/i });
    expect(connectBtn()).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Bot Token'), { target: { value: 'xoxb-dummy' } });
    expect(connectBtn()).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Signing Secret'), { target: { value: 'dummy-secret' } });
    expect(connectBtn()).toBeEnabled();
  });

  test('connecting PUTs the credentials, shows success, and calls onConnected', async () => {
    const spy = mockFetch();
    const onConnected = vi.fn();
    renderWizard(onConnected);
    await userEvent.click(screen.getByRole('button', { name: /I have my credentials/i }));
    fireEvent.change(screen.getByLabelText('Bot Token'), { target: { value: 'xoxb-dummy' } });
    fireEvent.change(screen.getByLabelText('Signing Secret'), { target: { value: 'dummy-secret' } });
    await userEvent.click(screen.getByRole('button', { name: /Connect Slack/i }));

    await waitFor(() =>
      expect(
        spy.mock.calls.some(([u, i]) => {
          if (!String(u).includes('/v1/org/org-1/gateway/config') || (i as RequestInit)?.method !== 'PUT') return false;
          const body = JSON.parse((i as RequestInit).body as string);
          return body.slack?.bot_token === 'xoxb-dummy' && body.slack?.signing_secret === 'dummy-secret';
        }),
      ).toBe(true),
    );
    expect(await screen.findByText(/Slack Connected!/i)).toBeInTheDocument();
    expect(onConnected).toHaveBeenCalledTimes(1);
  });

  test('a failed connection surfaces the error message', async () => {
    mockFetch(false);
    renderWizard();
    await userEvent.click(screen.getByRole('button', { name: /I have my credentials/i }));
    fireEvent.change(screen.getByLabelText('Bot Token'), { target: { value: 'xoxb-dummy' } });
    fireEvent.change(screen.getByLabelText('Signing Secret'), { target: { value: 'dummy-secret' } });
    await userEvent.click(screen.getByRole('button', { name: /Connect Slack/i }));
    expect(await screen.findByText(/Connection failed\. Check credentials\./i)).toBeInTheDocument();
    // Still on the form step — no success screen.
    expect(screen.queryByText(/Slack Connected!/i)).not.toBeInTheDocument();
  });
});
