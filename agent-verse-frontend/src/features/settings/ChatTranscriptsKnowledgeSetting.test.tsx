import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ChatTranscriptsKnowledgeSetting } from './ChatTranscriptsKnowledgeSetting';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function renderSetting() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ChatTranscriptsKnowledgeSetting />
    </QueryClientProvider>,
  );
}

const NAME = /Chat transcripts as knowledge/i;

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ChatTranscriptsKnowledgeSetting', () => {
  test('shows the stored (default off) state', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ enabled: false }));
    renderSetting();
    const sw = screen.getByRole('switch', { name: NAME });
    await waitFor(() => expect(sw).toBeEnabled());
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });

  test('turning it on PUTs the tenant switch', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      json({ enabled: (init?.method ?? 'GET') === 'PUT' }),
    );
    renderSetting();
    const sw = screen.getByRole('switch', { name: NAME });
    await waitFor(() => expect(sw).toBeEnabled());
    await userEvent.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'true'));
    const put = spy.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(String(put?.[0])).toMatch(/\/tenants\/me\/chat-transcripts-knowledge$/);
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ enabled: true });
  });

  test('turning it off reports what was removed and what a legal hold kept', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      (init?.method ?? 'GET') === 'PUT'
        ? json({ enabled: false, removed_documents: 3, held_documents: 1, pending: false })
        : json({ enabled: true }),
    );
    renderSetting();
    const sw = screen.getByRole('switch', { name: NAME });
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'true'));
    await userEvent.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'false'));
    expect(screen.getByText(/Removed 3 indexed transcripts; 1 kept under legal hold/)).toBeInTheDocument();
  });

  test('a non-admin refusal is shown and the switch stays off', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      (init?.method ?? 'GET') === 'PUT' ? json({ detail: 'Admin role required' }, 403) : json({ enabled: false }),
    );
    renderSetting();
    const sw = screen.getByRole('switch', { name: NAME });
    await waitFor(() => expect(sw).toBeEnabled());
    await userEvent.click(sw);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/Admin role required/));
    expect(sw).toHaveAttribute('aria-checked', 'false');
  });
});
