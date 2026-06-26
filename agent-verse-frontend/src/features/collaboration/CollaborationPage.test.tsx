import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { CollaborationPage } from './CollaborationPage';

vi.mock('@/lib/ws/useCollabSocket', () => ({
  useCollabSocket: ({ onOpen }: { onOpen?: () => void }) => {
    setTimeout(() => onOpen?.(), 0);
    return { sendMessage: vi.fn() };
  },
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <CollaborationPage />
    </QueryClientProvider>
  );
}

describe('CollaborationPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      apiKey: 'key-a',
      tenantId: 'tid-a',
      plan: 'free',
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  test('lists collaboration sessions', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            session_id: 'session-1',
            name: 'Jira review',
            mode: 'review',
            participants: ['human:lead'],
            participant_count: 1,
            status: 'active',
            content: '',
            goal_id: 'goal-1',
            agent_id: 'agent-1',
            created_at: '2026-06-25T00:00:00+00:00',
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    );

    renderPage();

    expect(await screen.findByText('Jira review')).toBeInTheDocument();
    expect(screen.getByText('goal-1')).toBeInTheDocument();
    expect(screen.getByText('agent-1')).toBeInTheDocument();
  });

  test('creates and opens a collaboration session', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          session_id: 'session-2',
          name: 'New review',
          mode: 'review',
          participants: ['human:lead', 'agent:jira'],
          participant_count: 2,
          status: 'active',
          content: 'Draft',
          goal_id: 'goal-2',
          agent_id: 'agent-2',
          created_at: '2026-06-25T00:00:00+00:00',
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } }
      )
    );
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    );

    renderPage();

    await userEvent.click(await screen.findByText('+ New Session'));
    await userEvent.type(screen.getByPlaceholderText('Session name'), 'New review');
    await userEvent.type(screen.getByPlaceholderText('Goal ID'), 'goal-2');
    await userEvent.type(screen.getByPlaceholderText('Agent ID'), 'agent-2');
    await userEvent.type(screen.getByPlaceholderText('Participants, comma separated'), 'human:lead,agent:jira');
    await userEvent.click(screen.getByText('Create Session'));

    await waitFor(() => expect(screen.getByText('Shared draft')).toBeInTheDocument());
    expect(screen.getByDisplayValue('Draft')).toBeInTheDocument();
  });
});
