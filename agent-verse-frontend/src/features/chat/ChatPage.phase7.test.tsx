/**
 * Phase 7 — ChatPage mounts the remaining orphan components and the schedule
 * card, each driven by its real trigger (SSE event, dispatch result, or a
 * clearly-reachable header control).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ChatPage from './ChatPage';

const mocks = vi.hoisted(() => ({
  sendMessage: vi.fn(() =>
    Promise.resolve({ intent: 'GOAL', message_id: 'm1', session_id: 's1', clarify_request: null, schedule_confirmation: null }),
  ),
  updateSession: vi.fn(() => Promise.resolve({ id: 's1' })),
  getUsage: vi.fn(() =>
    Promise.resolve({
      session_id: 's1',
      total_tokens: 1234,
      total_tokens_in: 1000,
      total_tokens_out: 234,
      total_cost_usd: 0.0123,
      llm_calls: 3,
    }),
  ),
  summarizeSession: vi.fn(() => Promise.resolve({ summary: 'We shipped the feature.' })),
}));
const { updateSession, getUsage } = mocks;

vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    listSessions: () => Promise.resolve({ sessions: [{
      id: 's1', tenant_id: 't1', title: 'My Session', pinned: false, ttl_days: null,
      system_prompt: null, agent_id: null, folder_id: null, show_reasoning: false,
      proactive_suggestions: true, preferred_model: null,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }] }),
    listFolders: () => Promise.resolve({ folders: [] }),
    listMessages: () => Promise.resolve({ messages: [] }),
    listModels: () => Promise.resolve({ models: ['gpt-4o', 'claude-3-5-sonnet'] }),
    sendMessage: mocks.sendMessage,
    updateSession: mocks.updateSession,
    getUsage: mocks.getUsage,
    summarizeSession: mocks.summarizeSession,
    streamUrl: () => 'http://test/stream',
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

// Capture EventSource instances so the test can push SSE events.
class MockES {
  static instances: MockES[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    MockES.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(obj: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(obj) }));
  }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/chat/s1']}>
        <Routes>
          <Route path="/chat/:sessionId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function sendMsg() {
  const input = await screen.findByLabelText('Chat message input');
  await userEvent.type(input, 'do the thing');
  fireEvent.click(screen.getByTestId('send-button'));
  await waitFor(() => expect(MockES.instances.length).toBeGreaterThan(0));
  return MockES.instances[MockES.instances.length - 1];
}

beforeEach(() => {
  MockES.instances = [];
  updateSession.mockClear();
  vi.stubGlobal('EventSource', MockES as unknown as typeof EventSource);
  // ConnectedServicesPanel fetches /chat/services on mount.
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ services: [] }) } as Response)),
  );
});

describe('ChatPage phase 7 — header controls', () => {
  it('shows the ChatModelSelector and persists a model change via the session-update path', async () => {
    renderPage();
    const btn = await screen.findByLabelText('Select model');
    await userEvent.click(btn);
    // Scope to the header dropdown's listbox (the composer has a native <select>).
    const listbox = screen.getByRole('listbox');
    await userEvent.click(within(listbox).getByRole('option', { name: 'claude-3-5-sonnet' }));
    expect(updateSession).toHaveBeenCalledWith('s1', { preferred_model: 'claude-3-5-sonnet' });
  });

  it('opens the ChatSessionSettingsModal from the header cog', async () => {
    renderPage();
    await userEvent.click(await screen.findByLabelText('Session settings'));
    expect(screen.getByRole('dialog', { name: 'Session settings' })).toBeDefined();
  });

  it('toggles the ConnectedServicesPanel from the header', async () => {
    renderPage();
    await userEvent.click(await screen.findByLabelText('Connected services'));
    expect(await screen.findByText('Connected Services')).toBeDefined();
  });

  it('opens the ChatUsageModal from the usage button', async () => {
    renderPage();
    await userEvent.click(await screen.findByLabelText('Session usage'));
    expect(await screen.findByRole('dialog', { name: 'Session usage' })).toBeDefined();
    expect(getUsage).toHaveBeenCalledWith('s1');
  });

  it('renders the ChatConversationSummary after summarize', async () => {
    renderPage();
    await userEvent.click(await screen.findByLabelText('Summarize conversation'));
    expect(await screen.findByText('We shipped the feature.')).toBeDefined();
  });
});

describe('ChatPage phase 7 — SSE-driven cards', () => {
  it('renders schedule / cost / goal-summary cards from their events', async () => {
    renderPage();
    const es = await sendMsg();

    act(() => {
      es.emit({ type: 'schedule_created', cron_expression: '0 9 * * *', human_schedule: 'Every day at 9am', next_run_iso: '2026-09-16T09:00:00Z' });
      es.emit({ type: 'usage', tokens_in: 10, tokens_out: 20, cost_usd: 0.001 });
      es.emit({ type: 'goal_complete', summary: 'All done!', suggestions: ['Do more'] });
      es.emit({ type: 'done' });
    });

    expect(await screen.findByTestId('chat-schedule-card')).toBeDefined();
    expect(screen.getByText('Every day at 9am')).toBeDefined();
    expect(screen.getByText('Goal completed')).toBeDefined();
    expect(screen.getByText('All done!')).toBeDefined();
    // Cost badge shows the total token count (10 + 20).
    expect(screen.getByText('30')).toBeDefined();
  });

  it('renders the ChatClarifyCard from a clarify_needed event', async () => {
    renderPage();
    const es = await sendMsg();
    act(() => {
      es.emit({ type: 'clarify_needed', question: 'Which environment?', options: ['staging', 'prod'], round: 1 });
    });
    expect(await screen.findByText('Which environment?')).toBeDefined();
    expect(screen.getByText('staging')).toBeDefined();
  });

  it('renders the ChatGoalFailureCard from a failure_analysis event', async () => {
    renderPage();
    const es = await sendMsg();
    act(() => {
      es.emit({ type: 'failure_analysis', reason: 'Tool timed out', suggestions: ['Retry now'] });
      es.emit({ type: 'done' });
    });
    expect(await screen.findByText('Goal failed')).toBeDefined();
    expect(screen.getByText('Tool timed out')).toBeDefined();
    expect(screen.getByText('Retry now')).toBeDefined();
  });
});
