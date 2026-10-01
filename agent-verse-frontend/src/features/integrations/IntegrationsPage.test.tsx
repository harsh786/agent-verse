import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { useToastStore } from '@/stores/toast';
import { IntegrationsPage } from './IntegrationsPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <IntegrationsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem('av_api_key', 'test-key');
  useAuthStore.setState({ apiKey: 'test-key', tenantId: 't', plan: 'free', isAuthenticated: true });
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
});
afterEach(() => vi.restoreAllMocks());

describe('IntegrationsPage', () => {
  test('shows the four providers with their endpoint paths', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(screen.getByText('Slack')).toBeInTheDocument();
    expect(screen.getByText('Zapier')).toBeInTheDocument();
    expect(screen.getByText('Alertmanager')).toBeInTheDocument();
    expect(screen.getByText('Datadog')).toBeInTheDocument();
    expect(screen.getByText(/\/integrations\/slack\/commands/)).toBeInTheDocument();
    expect(screen.getByText(/\/integrations\/events\/datadog/)).toBeInTheDocument();
  });

  test('copy button writes the endpoint URL to the clipboard', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /copy endpoint/i })[0]);
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalled());
  });

  test('copy button shows an error toast when the clipboard write is rejected', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    renderPage();
    await userEvent.click(screen.getAllByRole('button', { name: /copy endpoint/i })[0]);
    await waitFor(() =>
      expect(
        useToastStore.getState().toasts.some((t) => t.message === 'Could not copy — copy it manually.')
      ).toBe(true)
    );
  });

  test('shows the empty-state message when no zapier goals are available', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }));
    renderPage();
    expect(
      await screen.findByText('No completed goals available to the Zapier poll trigger.')
    ).toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });

  test('falls back to id/goal_id/dash when a zapier goal entry has no goal_id, goal, or id', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/integrations/zapier/goals'))
        return new Response(JSON.stringify([{ status: 'running' }]), { status: 200 });
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('—')).toBeInTheDocument();
  });

  test('uses the id as the list key when goal_id is absent on a zapier goal entry', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/integrations/zapier/goals'))
        return new Response(
          JSON.stringify([{ id: 'fallback-id', goal: 'Investigate outage', status: 'complete' }]),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('Investigate outage')).toBeInTheDocument();
  });

  test('TRG-02: shows the Slack workspaces bound to this tenant, not SLACK_TENANT_ID', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/channels/mappings'))
        return new Response(
          JSON.stringify([
            { id: 'm1', channel_type: 'slack', channel_id: 'T-BOUND', status: 'verified' },
            { id: 'm2', channel_type: 'slack', channel_id: 'T-PENDING', status: 'pending_verification' },
            { id: 'm3', channel_type: 'teams', channel_id: 'not-slack', status: 'verified' },
          ]),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('T-BOUND')).toBeInTheDocument();
    expect(screen.getByText('bound')).toBeInTheDocument();
    expect(screen.getByText('not routing (pending_verification)')).toBeInTheDocument();
    expect(screen.queryByText('not-slack')).not.toBeInTheDocument();
    expect(screen.queryByText('SLACK_TENANT_ID')).not.toBeInTheDocument();
  });

  test('TRG-36: "Link my Slack account" issues a code and shows the /agentverse link command', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/channels/identities/link-codes') && init?.method === 'POST')
        return new Response(
          JSON.stringify({
            id: 'l1',
            status: 'pending',
            code: 'ABCD234567',
            expires_at: new Date(Date.now() + 600_000).toISOString(),
            instructions: 'In Slack, run: /agentverse link ABCD234567',
          }),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    await userEvent.click(screen.getByRole('button', { name: /link my slack account/i }));
    expect(await screen.findByText('/agentverse link ABCD234567')).toBeInTheDocument();
    expect(
      fetchSpy.mock.calls.some(
        ([u, i]) => String(u).includes('/channels/identities/link-codes') && i?.method === 'POST'
      )
    ).toBe(true);
  });

  test('TRG-36: lists linked Slack accounts and unlinks one', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes('/channels/identities/l9') && init?.method === 'DELETE')
        return new Response(JSON.stringify({ id: 'l9', deleted: true }), { status: 200 });
      if (url.includes('/channels/identities'))
        return new Response(
          JSON.stringify([
            {
              id: 'l9',
              principal_id: 'key-alice',
              team_id: 'T1',
              slack_user_id: 'U-ALICE',
              status: 'active',
            },
          ]),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('U-ALICE')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /unlink u-alice/i }));
    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(
          ([u, i]) => String(u).includes('/channels/identities/l9') && i?.method === 'DELETE'
        )
      ).toBe(true)
    );
  });

  test('shows Zapier delivery from /integrations/zapier/goals', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/integrations/zapier/goals'))
        return new Response(
          JSON.stringify([{ goal_id: 'g1', goal: 'Resolve incident', status: 'complete' }]),
          { status: 200 }
        );
      return new Response('[]', { status: 200 });
    });
    renderPage();
    expect(await screen.findByText('Resolve incident')).toBeInTheDocument();
  });
});
