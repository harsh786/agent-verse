import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { useAuthStore } from '@/stores/auth';
import { ConversationViewer } from './ConversationViewer';

const CONVERSATIONS = [
  {
    conversation_id: 'conv-1', channel: 'telegram', actor_name: 'Alice', turn_count: 2,
    last_message: 'ship it', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T01:00:00Z',
    turns: [
      { role: 'user', content: 'Deploy please', timestamp: '2026-01-01T00:00:00Z' },
      { role: 'assistant', content: 'Deployment started', timestamp: '2026-01-01T00:01:00Z' },
    ],
  },
  {
    conversation_id: 'conv-2', channel: 'slack', actor_name: 'Bob', turn_count: 1,
    last_message: 'status?', created_at: '2026-01-02T00:00:00Z', updated_at: '2026-01-02T00:00:00Z',
  },
];

function mockConversations(list: unknown[] = CONVERSATIONS) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/commands'))
      return new Response(JSON.stringify({ conversations: list }), { status: 200, headers: { 'Content-Type': 'application/json' } });
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
  test('lists conversations and shows the placeholder before selecting one', async () => {
    mockConversations();
    renderViewer();
    expect(await screen.findByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(screen.getByText('Select a conversation to inspect')).toBeInTheDocument();
  });

  test('selecting a conversation renders its turns in the detail pane', async () => {
    mockConversations();
    renderViewer();
    await userEvent.click(await screen.findByText('Alice'));
    expect(await screen.findByText('Deploy please')).toBeInTheDocument();
    expect(screen.getByText('Deployment started')).toBeInTheDocument();
  });

  test('selecting a conversation without loaded turns shows the fallback note', async () => {
    mockConversations();
    renderViewer();
    await userEvent.click(await screen.findByText('Bob'));
    expect(await screen.findByText(/Turn details not available/i)).toBeInTheDocument();
  });

  test('shows the empty state when there are no conversations', async () => {
    mockConversations([]);
    renderViewer();
    expect(await screen.findByText('No conversations yet.')).toBeInTheDocument();
  });

  test('the search box filters the conversation list', async () => {
    mockConversations();
    renderViewer();
    await screen.findByText('Alice');
    await userEvent.type(screen.getByLabelText('Search conversations'), 'Bob');
    await waitFor(() => expect(screen.queryByText('Alice')).not.toBeInTheDocument());
    expect(screen.getByText('Bob')).toBeInTheDocument();
  });
});
