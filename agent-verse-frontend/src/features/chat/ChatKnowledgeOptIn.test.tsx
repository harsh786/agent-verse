import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ChatKnowledgeOptIn } from './ChatKnowledgeOptIn';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const OFF = { tenant_enabled: true, opted_in: false, opted_in_at: null, revoked_at: null };
const NAME = /Use my chats as knowledge/i;

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ChatKnowledgeOptIn', () => {
  test('is off by default and cannot be turned on while the workspace switch is off', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ ...OFF, tenant_enabled: false }));
    render(<ChatKnowledgeOptIn />);
    expect(await screen.findByText(/an admin turns it on in Settings/i)).toBeInTheDocument();
    const sw = screen.getByRole('switch', { name: NAME });
    expect(sw).toHaveAttribute('aria-checked', 'false');
    expect(sw).toBeDisabled();
  });

  test('opting in PUTs my own opt-in', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      (init?.method ?? 'GET') === 'PUT'
        ? json({ opted_in: true, opted_in_at: '2026-10-06T09:00:00Z', revoked_at: null })
        : json(OFF),
    );
    render(<ChatKnowledgeOptIn />);
    const sw = screen.getByRole('switch', { name: NAME });
    await waitFor(() => expect(sw).toBeEnabled());
    await userEvent.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'true'));
    const put = spy.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(String(put?.[0])).toMatch(/\/chat\/settings\/knowledge$/);
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ opted_in: true });
  });

  test('revoking reports what was removed and what a legal hold kept', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      (init?.method ?? 'GET') === 'PUT'
        ? json({ opted_in: false, opted_in_at: null, revoked_at: '2026-10-06T10:00:00Z',
                 removed_documents: 2, held_documents: 1, held_document_ids: ['d1'], pending: false })
        : json({ ...OFF, opted_in: true }),
    );
    render(<ChatKnowledgeOptIn />);
    const sw = screen.getByRole('switch', { name: NAME });
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'true'));
    await userEvent.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'false'));
    expect(screen.getByText(/Removed 2 indexed transcripts; 1 kept under legal hold/)).toBeInTheDocument();
  });

  test('a caller that is not a person sees why, and nothing is toggled', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      json({ detail: 'Only a signed-in person can opt their own chats in or out of knowledge' }, 403),
    );
    render(<ChatKnowledgeOptIn />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/Only a signed-in person/);
    expect(screen.getByRole('switch', { name: NAME })).toBeDisabled();
  });

  test('a failed revocation is shown, not faked', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) =>
      (init?.method ?? 'GET') === 'PUT'
        ? json({ detail: 'Your opt-in is revoked (nothing more is indexed), but removing failed' }, 503)
        : json({ ...OFF, opted_in: true }),
    );
    render(<ChatKnowledgeOptIn />);
    const sw = screen.getByRole('switch', { name: NAME });
    await waitFor(() => expect(sw).toHaveAttribute('aria-checked', 'true'));
    await userEvent.click(sw);
    expect(await screen.findByRole('alert')).toHaveTextContent(/removing failed/);
  });
});
