import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import React, { type ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';

// The wizard uses <AnimatePresence mode="wait"> to gate each step on the previous
// step's exit animation, which never completes under jsdom. Reduce framer-motion
// to plain elements so step transitions are synchronous (repo-standard pattern).
vi.mock('framer-motion', async (importOriginal) => {
  const actual = await importOriginal<typeof import('framer-motion')>();
  // Strip framer-motion-only props so React doesn't warn, and render a plain tag.
  const makeStub = (tag: string) =>
    ({ children, initial, animate, exit, transition, whileTap, whileHover, layout, ...props }:
      { children?: ReactNode; [k: string]: unknown }) =>
      React.createElement(tag, props as Record<string, unknown>, children);
  // Cache stubs by key so each `motion.div` keeps a STABLE component identity
  // across renders — otherwise inputs remount on every keystroke and lose focus.
  const stubCache: Record<string, unknown> = {};
  const motionProxy = new Proxy({}, {
    get: (_t, key: string) => (stubCache[key] ??= makeStub(key)),
  });
  return {
    ...actual,
    useReducedMotion: () => true,
    AnimatePresence: ({ children }: { children?: ReactNode }) => <>{children}</>,
    motion: motionProxy,
  };
});

import { TelegramSetup } from './TelegramSetup';

function mockGatewayPut(ok = true) {
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
      <MemoryRouter><TelegramSetup orgId="org-1" onConnected={onConnected} /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('TelegramSetup', () => {
  test('starts on the create-bot step with BotFather instructions', () => {
    mockGatewayPut();
    renderWizard();
    expect(screen.getByRole('heading', { name: 'Connect Telegram Bot' })).toBeInTheDocument();
    expect(screen.getByText('@BotFather')).toBeInTheDocument();
  });

  test('advances through the steps and enables the token input', async () => {
    mockGatewayPut();
    renderWizard();
    await userEvent.click(screen.getByRole('button', { name: /I have my bot token/i }));
    const tokenInput = screen.getByLabelText('Bot Token');
    expect(tokenInput).toBeInTheDocument();
    await userEvent.type(tokenInput, '123:ABC');
    await userEvent.click(screen.getByRole('button', { name: /Continue/i }));
    // Step 3 asks for authorized user IDs.
    expect(await screen.findByLabelText('Telegram user ID')).toBeInTheDocument();
  });

  test('completing the wizard PUTs the config and reaches the done step', async () => {
    const spy = mockGatewayPut();
    const onConnected = vi.fn();
    renderWizard(onConnected);
    await userEvent.click(screen.getByRole('button', { name: /I have my bot token/i }));
    await userEvent.type(screen.getByLabelText('Bot Token'), '123:ABC');
    await userEvent.click(screen.getByRole('button', { name: /Continue/i }));
    await userEvent.type(await screen.findByLabelText('Telegram user ID'), '99887766');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await userEvent.click(screen.getByRole('button', { name: /Connect Telegram Bot/i }));

    await waitFor(() =>
      expect(spy.mock.calls.some(([u, i]) => {
        if (!String(u).includes('/v1/org/org-1/gateway/config') || (i as RequestInit)?.method !== 'PUT') return false;
        const body = JSON.parse((i as RequestInit).body as string);
        return body.telegram?.bot_token === '123:ABC' && body.telegram?.authorized_user_ids?.includes('99887766');
      })).toBe(true),
    );
    expect(await screen.findByText('Telegram Connected!')).toBeInTheDocument();
    expect(onConnected).toHaveBeenCalled();
  });

  test('shows an error message when the connect request fails', async () => {
    mockGatewayPut(false);
    renderWizard();
    await userEvent.click(screen.getByRole('button', { name: /I have my bot token/i }));
    await userEvent.type(screen.getByLabelText('Bot Token'), '123:ABC');
    await userEvent.click(screen.getByRole('button', { name: /Continue/i }));
    await userEvent.type(await screen.findByLabelText('Telegram user ID'), '99887766');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await userEvent.click(screen.getByRole('button', { name: /Connect Telegram Bot/i }));
    expect(await screen.findByText(/Failed to connect/i)).toBeInTheDocument();
  });
});
