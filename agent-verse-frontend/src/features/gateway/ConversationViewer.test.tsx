import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConversationViewer } from './ConversationViewer';

// The REAL shape of GET /v1/org/{id}/commands (app/org/router.py::org_list_commands,
// rows from OrgCommandStore): newest-first command records, no `conversations` key.
const COMMANDS = [
  {
    command_id: 'c3', command: 'ship it', channel: 'telegram', org_id: 'org-1', tenant_id: 't',
    status: 'completed', requires_2fa: false, conversation_id: 'chat-alice', goal_id: 'g-3',
    result: null, submitted_at: '2026-01-01T01:00:00Z',
  },
  {
    command_id: 'c2', command: 'status?', channel: 'slack', org_id: 'org-1', tenant_id: 't',
    status: 'queued', requires_2fa: false, conversation_id: 'C-bob', result: null,
    submitted_at: '2026-01-01T00:30:00Z',
  },
  {
    command_id: 'c1', command: 'Deploy please', channel: 'telegram', org_id: 'org-1', tenant_id: 't',
    status: 'failed', requires_2fa: false, conversation_id: 'chat-alice', error: 'boom',
    result: null, submitted_at: '2026-01-01T00:00:00Z',
  },
  {
    command_id: 'c0', command: 'one-off via rest', channel: 'rest', org_id: 'org-1', tenant_id: 't',
    status: 'completed', requires_2fa: false, conversation_id: null, result: null,
    submitted_at: '2025-12-31T00:00:00Z',
  },
];

function mockCommands(list: unknown[] = COMMANDS, status = 200) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/commands'))
      return new Response(
        JSON.stringify(status === 200 ? { org_id: 'org-1', commands: list, total: list.length } : { detail: 'nope' }),
        { status, headers: { 'Content-Type': 'application/json' } },
      );
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
}

function renderViewer() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><ConversationViewer orgId="org-1" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear();
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('ConversationViewer', () => {
  test('calls the real commands endpoint (no invented query params)', async () => {
    const spy = mockCommands();
    renderViewer();
    await screen.findByText('chat-alice');
    const urls = spy.mock.calls.map(([u]) => String(u));
    expect(urls.some(u => u.endsWith('/v1/org/org-1/commands?limit=100'))).toBe(true);
    expect(urls.some(u => u.includes('include_conversations'))).toBe(false);
  });

  test('groups the `commands` array into conversations by conversation_id', async () => {
    mockCommands();
    renderViewer();
    const alice = await screen.findByText('chat-alice');
    expect(screen.getByText('C-bob')).toBeInTheDocument();
    // A command without a conversation_id is its own single-command entry.
    expect(screen.getByText('one-off via rest')).toBeInTheDocument();
    // Two telegram commands share chat-alice; the newest is the preview.
    const row = alice.closest('button')!;
    expect(row).toHaveTextContent('2 cmds');
    expect(row).toHaveTextContent('ship it');
    expect(screen.getByText('Select a conversation to inspect')).toBeInTheDocument();
  });

  test('selecting a conversation lists its commands oldest-first with status', async () => {
    mockCommands();
    renderViewer();
    await userEvent.click(await screen.findByText('chat-alice'));
    const first = await screen.findByText('Deploy please');
    const second = screen.getAllByText('ship it').at(-1)!;
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
    // Replies are not part of command history — say so rather than faking them.
    expect(screen.getByText(/Assistant replies are not recorded/i)).toBeInTheDocument();
  });

  test('shows the empty state when there are no commands', async () => {
    mockCommands([]);
    renderViewer();
    expect(await screen.findByText('No conversations yet.')).toBeInTheDocument();
  });

  test('a failed request shows an error, not a fake empty list', async () => {
    mockCommands([], 403);
    renderViewer();
    expect(await screen.findByText(/Couldn.t load conversations/i)).toBeInTheDocument();
    expect(screen.queryByText('No conversations yet.')).not.toBeInTheDocument();
  });

  test('the search box filters by command text or conversation id', async () => {
    mockCommands();
    renderViewer();
    await screen.findByText('chat-alice');
    await userEvent.type(screen.getByLabelText('Search conversations'), 'bob');
    await waitFor(() => expect(screen.queryByText('chat-alice')).not.toBeInTheDocument());
    expect(screen.getByText('C-bob')).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText('Search conversations'));
    await userEvent.type(screen.getByLabelText('Search conversations'), 'deploy');
    expect(await screen.findByText('chat-alice')).toBeInTheDocument();
    expect(screen.queryByText('C-bob')).not.toBeInTheDocument();
  });
});
