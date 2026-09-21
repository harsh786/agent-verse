/**
 * Additional coverage for ChatPage: session select/rename from the sidebar,
 * the cost badge's click/keyboard-activated usage shortcut, empty-thread
 * suggestion select, clarify/goal-summary/goal-failure card callbacks, and
 * closing the settings/usage modals.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ChatPage from './ChatPage';

const baseSession = {
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
  preferred_model: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const mocks = vi.hoisted(() => ({
  sendMessage: vi.fn(() =>
    Promise.resolve({ intent: 'QA', message_id: 'm1', session_id: 's1', clarify_request: null, schedule_confirmation: null }),
  ),
  updateSession: vi.fn(() => Promise.resolve({ ...baseSession })),
  getUsage: vi.fn(() =>
    Promise.resolve({
      session_id: 's1',
      total_tokens: 30,
      total_tokens_in: 10,
      total_tokens_out: 20,
      total_cost_usd: 0.001,
      llm_calls: 1,
    }),
  ),
}));

vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    listSessions: () => Promise.resolve({ sessions: [baseSession] }),
    listFolders: () => Promise.resolve({ folders: [] }),
    listMessages: () => Promise.resolve({ messages: [] }),
    listModels: () => Promise.resolve({ models: ['gpt-4o'] }),
    sendMessage: mocks.sendMessage,
    updateSession: mocks.updateSession,
    getUsage: mocks.getUsage,
    streamUrl: () => 'http://test/stream',
  },
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
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

function renderPage(path = '/chat/s1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
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
  mockNavigate.mockClear();
  mocks.sendMessage.mockClear();
  mocks.updateSession.mockClear();
  mocks.getUsage.mockClear();
  MockES.instances = [];
  vi.stubGlobal('EventSource', MockES as unknown as typeof EventSource);
});

describe('ChatPage — sidebar session select / rename', () => {
  it('re-selecting the active session calls navigate via handleSelectSession', async () => {
    renderPage();
    const row = await screen.findByTestId('session-s1');
    fireEvent.click(row);
    expect(mockNavigate).toHaveBeenCalledWith('/chat/s1');
  });

  it('renames a session from the sidebar edit control', async () => {
    renderPage();
    const row = await screen.findByTestId('session-s1');
    fireEvent.click(within(row).getByLabelText('Rename session'));
    const input = within(row).getByLabelText('Rename session');
    fireEvent.change(input, { target: { value: 'Renamed' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(mocks.updateSession).toHaveBeenCalledWith('s1', { title: 'Renamed' }));
  });
});

describe('ChatPage — cost badge shortcut to usage modal', () => {
  it('opens the usage modal on click', async () => {
    renderPage();
    const es = await sendMsg();
    act(() => {
      es.emit({ type: 'usage', tokens_in: 10, tokens_out: 20, cost_usd: 0.001 });
      es.emit({ type: 'done' });
    });
    const badge = await screen.findByLabelText('Open session usage');
    fireEvent.click(badge);
    expect(await screen.findByRole('dialog', { name: 'Session usage' })).toBeDefined();
    expect(mocks.getUsage).toHaveBeenCalledWith('s1');
  });

  it('opens the usage modal on Enter/Space keydown', async () => {
    renderPage();
    const es = await sendMsg();
    act(() => {
      es.emit({ type: 'usage', tokens_in: 10, tokens_out: 20, cost_usd: 0.001 });
      es.emit({ type: 'done' });
    });
    const badge = await screen.findByLabelText('Open session usage');
    fireEvent.keyDown(badge, { key: 'Enter' });
    expect(await screen.findByRole('dialog', { name: 'Session usage' })).toBeDefined();
  });
});

describe('ChatPage — empty-thread suggestion select', () => {
  it('sending a suggestion from the empty thread state calls handleSend', async () => {
    renderPage();
    const suggestion = await screen.findByText('Ask a question');
    fireEvent.click(suggestion);
    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalled());
    const [, content] = mocks.sendMessage.mock.calls[0] as unknown as [string, string];
    expect(content).toBe('What is the difference between SQL and NoSQL?');
  });
});

describe('ChatPage — clarify / goal-summary / goal-failure card callbacks', () => {
  it('answering a clarify card re-sends via handleSend', async () => {
    renderPage();
    const es = await sendMsg('help me decide');
    act(() => {
      es.emit({ type: 'clarify_needed', question: 'Which env?', options: ['staging', 'prod'], round: 1 });
      es.emit({ type: 'done' });
    });
    mocks.sendMessage.mockClear();
    fireEvent.click(await screen.findByText('staging'));
    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalled());
    const [, content] = mocks.sendMessage.mock.calls[0] as unknown as [string, string];
    expect(content).toBe('staging');
  });

  it('clicking a goal-summary suggestion re-sends via handleSend', async () => {
    renderPage();
    const es = await sendMsg('finish the task');
    act(() => {
      es.emit({ type: 'goal_complete', summary: 'Done deal', suggestions: ['Do the next thing'] });
      es.emit({ type: 'done' });
    });
    mocks.sendMessage.mockClear();
    fireEvent.click(await screen.findByText('Do the next thing'));
    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalled());
    const [, content] = mocks.sendMessage.mock.calls[0] as unknown as [string, string];
    expect(content).toBe('Do the next thing');
  });

  it('retrying a goal-failure suggestion re-sends via handleSend', async () => {
    renderPage();
    const es = await sendMsg('deploy the thing');
    act(() => {
      es.emit({ type: 'failure_analysis', reason: 'Tool timed out', suggestions: ['Retry with more time'] });
      es.emit({ type: 'done' });
    });
    mocks.sendMessage.mockClear();
    fireEvent.click(await screen.findByText('Retry with more time'));
    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalled());
    const [, content] = mocks.sendMessage.mock.calls[0] as unknown as [string, string];
    expect(content).toBe('Retry with more time');
  });
});

describe('ChatPage — modal close handlers', () => {
  it('closes the session settings modal', async () => {
    renderPage();
    await userEvent.click(await screen.findByLabelText('Session settings'));
    const dialog = screen.getByRole('dialog', { name: 'Session settings' });
    fireEvent.click(within(dialog).getByLabelText('Close'));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Session settings' })).not.toBeInTheDocument());
  });

  it('closes the usage modal and clears the usage summary', async () => {
    renderPage();
    await userEvent.click(await screen.findByLabelText('Session usage'));
    const dialog = await screen.findByRole('dialog', { name: 'Session usage' });
    fireEvent.click(within(dialog).getByLabelText('Close'));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Session usage' })).not.toBeInTheDocument());
  });
});
