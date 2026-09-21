/**
 * Final branch top-up: goal/failure message fallback chains, preferred_model
 * adoption on mount, the session-settings Save path, and an SSE
 * schedule_created card built from human_schedule + next_run (no cron_expression).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ChatPage from './ChatPage';

const sessionWithModel = {
  id: 's1',
  tenant_id: 't1',
  title: 'My Session',
  pinned: false,
  ttl_days: null,
  system_prompt: null,
  agent_id: null,
  folder_id: null,
  show_reasoning: false,
  proactive_suggestions: true,
  preferred_model: 'claude-3-5-sonnet',
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const mocks = vi.hoisted(() => ({
  sendMessage: vi.fn(() =>
    Promise.resolve({ intent: 'QA', message_id: 'm1', session_id: 's1', clarify_request: null, schedule_confirmation: null }),
  ),
  updateSession: vi.fn(() => Promise.resolve({ ...sessionWithModel })),
}));

vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    listSessions: () => Promise.resolve({ sessions: [sessionWithModel] }),
    listFolders: () => Promise.resolve({ folders: [] }),
    listMessages: () => Promise.resolve({ messages: [] }),
    listModels: () => Promise.resolve({ models: ['gpt-4o', 'claude-3-5-sonnet'] }),
    sendMessage: mocks.sendMessage,
    updateSession: mocks.updateSession,
    streamUrl: () => 'http://test/stream',
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

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

async function sendMsg(text = 'do the thing') {
  const input = await screen.findByLabelText('Chat message input');
  await userEvent.type(input, text);
  fireEvent.click(screen.getByTestId('send-button'));
  await waitFor(() => expect(MockES.instances.length).toBeGreaterThan(0));
  return MockES.instances[MockES.instances.length - 1];
}

beforeEach(() => {
  mocks.sendMessage.mockClear();
  mocks.updateSession.mockClear();
  MockES.instances = [];
  vi.stubGlobal('EventSource', MockES as unknown as typeof EventSource);
});

describe('ChatPage — preferred_model adoption', () => {
  it('adopts the active session preferred_model on mount', async () => {
    renderPage();
    // The header model selector should show the session's saved model, not the
    // first model in the list (gpt-4o).
    expect(await screen.findByText('claude-3-5-sonnet')).toBeDefined();
  });
});

describe('ChatPage — session settings Save path', () => {
  it('persists settings via the update path when Save is clicked', async () => {
    renderPage();
    await userEvent.click(await screen.findByLabelText('Session settings'));
    const dialog = screen.getByRole('dialog', { name: 'Session settings' });
    fireEvent.click(within(dialog).getByText('Save'));
    await waitFor(() => expect(mocks.updateSession).toHaveBeenCalled());
  });
});

describe('ChatPage — goal/failure message fallback chains', () => {
  it('falls back to the default goal-complete message when summary/result are absent', async () => {
    renderPage();
    const es = await sendMsg();
    act(() => {
      es.emit({ type: 'goal_complete' });
      es.emit({ type: 'done' });
    });
    expect(await screen.findByText('Goal completed successfully.')).toBeDefined();
  });

  it('uses the failure "message" field when reason/analysis are absent', async () => {
    renderPage();
    const es = await sendMsg('deploy it');
    act(() => {
      es.emit({ type: 'failure_analysis', message: 'Timed out waiting for the tool.' });
      es.emit({ type: 'done' });
    });
    expect(await screen.findByText('Timed out waiting for the tool.')).toBeDefined();
  });

  it('falls back to the default failure message when reason/analysis/message are all absent', async () => {
    renderPage();
    const es = await sendMsg('deploy it again');
    act(() => {
      es.emit({ type: 'error', reason: '' });
      es.emit({ type: 'failure_analysis' });
      es.emit({ type: 'done' });
    });
    expect(await screen.findByText('The goal could not be completed.')).toBeDefined();
  });
});

describe('ChatPage — SSE schedule_created without a cron expression', () => {
  it('builds the schedule card from human_schedule + next_run when cron_expression is absent', async () => {
    renderPage();
    const es = await sendMsg('remind me daily');
    act(() => {
      es.emit({ type: 'schedule_created', human_schedule: 'Every day at noon', next_run: '2026-09-22T12:00:00Z' });
    });
    expect(await screen.findByTestId('chat-schedule-card')).toBeDefined();
    expect(screen.getByText('Every day at noon')).toBeDefined();
  });
});
