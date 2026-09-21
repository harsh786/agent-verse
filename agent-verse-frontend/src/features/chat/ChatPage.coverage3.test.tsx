/**
 * Branch-coverage top-up for ChatPage: dispatch-provided clarify/schedule
 * cards (as opposed to their SSE-driven counterparts), the numeric/label
 * fallback chains in num()/suggestionsOf()/toCostInfo(), the swallowed
 * catch branches (open artifact / open usage / summarize / list models),
 * the HITL-clearing terminal event types, and the cost badge's
 * Space-vs-other-key branch.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
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
  sendMessage: vi.fn(),
  listArtifacts: vi.fn(),
  artifactDownloadUrl: vi.fn(() => 'http://test/artifact/art1'),
  getUsage: vi.fn(),
  summarizeSession: vi.fn(),
  listModels: vi.fn(() => Promise.resolve({ models: ['gpt-4o'] })),
}));

vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    listSessions: () => Promise.resolve({ sessions: [baseSession] }),
    listFolders: () => Promise.resolve({ folders: [] }),
    listMessages: () => Promise.resolve({ messages: [] }),
    listModels: mocks.listModels,
    listArtifacts: mocks.listArtifacts,
    artifactDownloadUrl: mocks.artifactDownloadUrl,
    sendMessage: mocks.sendMessage,
    getUsage: mocks.getUsage,
    summarizeSession: mocks.summarizeSession,
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
  mocks.sendMessage.mockReset();
  mocks.sendMessage.mockResolvedValue({
    intent: 'QA',
    message_id: 'm1',
    session_id: 's1',
    clarify_request: null,
    schedule_confirmation: null,
  });
  mocks.listArtifacts.mockReset();
  mocks.getUsage.mockReset();
  mocks.getUsage.mockResolvedValue({
    session_id: 's1',
    total_tokens: 2,
    total_tokens_in: 1,
    total_tokens_out: 1,
    total_cost_usd: 0.0001,
    llm_calls: 1,
  });
  mocks.summarizeSession.mockReset();
  mocks.listModels.mockClear();
  MockES.instances = [];
  vi.stubGlobal('EventSource', MockES as unknown as typeof EventSource);
});

describe('ChatPage — dispatch-provided clarify/schedule (non-SSE)', () => {
  it('shows a clarify card immediately when the POST response carries clarify_request', async () => {
    mocks.sendMessage.mockResolvedValue({
      intent: 'GOAL',
      message_id: 'm1',
      session_id: 's1',
      clarify_request: { question: 'Which region?', options: ['us', 'eu'], round: 2 },
      schedule_confirmation: null,
    });
    renderPage();
    await sendMsg('deploy it');
    expect(await screen.findByText('Which region?')).toBeDefined();
  });

  it('shows a schedule card immediately when the POST response carries schedule_confirmation', async () => {
    mocks.sendMessage.mockResolvedValue({
      intent: 'GOAL',
      message_id: 'm1',
      session_id: 's1',
      clarify_request: null,
      schedule_confirmation: {
        cron_expression: '0 0 * * *',
        human_schedule: 'Every midnight',
        next_run_iso: '2026-09-22T00:00:00Z',
        goal_text: 'Backup DB',
      },
    });
    renderPage();
    await sendMsg('schedule the backup');
    expect(await screen.findByTestId('chat-schedule-card')).toBeDefined();
    expect(screen.getByText('Every midnight')).toBeDefined();
  });
});

describe('ChatPage — numeric/suggestion fallback fields', () => {
  it('reads cost info from the alternate field names (input_tokens/completion_tokens/cost)', async () => {
    renderPage();
    const es = await sendMsg();
    act(() => {
      es.emit({ type: 'usage', input_tokens: 5, completion_tokens: 8, cost: 0.2 });
      es.emit({ type: 'done' });
    });
    // 5 + 8 = 13 total tokens shown on the badge.
    expect(await screen.findByText('13')).toBeDefined();
  });

  it('falls back to proactive_suggestions follow_ups when goal_complete has no suggestions', async () => {
    renderPage();
    const es = await sendMsg();
    act(() => {
      es.emit({ type: 'proactive_suggestions', follow_ups: ['Check the logs'] });
      es.emit({ type: 'goal_complete', result: 'Shipped it.' });
      es.emit({ type: 'done' });
    });
    expect(await screen.findByText('Shipped it.')).toBeDefined();
    expect(screen.getByText('Check the logs')).toBeDefined();
  });
});

describe('ChatPage — swallowed catch branches', () => {
  it('does not crash when listArtifacts rejects on artifact open', async () => {
    mocks.listArtifacts.mockRejectedValue(new Error('boom'));
    renderPage();
    const es = await sendMsg('generate a report');
    act(() => {
      es.emit({ type: 'artifact_created', artifact_id: 'art1', title: 'Report' });
    });
    const openBtn = await screen.findByLabelText('Open Report');
    fireEvent.click(openBtn);
    await waitFor(() => expect(mocks.listArtifacts).toHaveBeenCalled());
    // No panel appears, and the page keeps working.
    expect(screen.queryByLabelText('Artifact panel')).not.toBeInTheDocument();
  });

  it('does not crash when getUsage rejects — modal stays unrendered without a summary', async () => {
    mocks.getUsage.mockRejectedValue(new Error('boom'));
    renderPage();
    await userEvent.click(await screen.findByLabelText('Session usage'));
    await waitFor(() => expect(mocks.getUsage).toHaveBeenCalledWith('s1'));
    // ChatUsageModal renders null without a summary — the catch swallowed the error.
    expect(screen.queryByRole('dialog', { name: 'Session usage' })).not.toBeInTheDocument();
  });

  it('does not crash and shows no summary when summarizeSession rejects', async () => {
    mocks.summarizeSession.mockRejectedValue(new Error('boom'));
    renderPage();
    await userEvent.click(await screen.findByLabelText('Summarize conversation'));
    await waitFor(() => expect(mocks.summarizeSession).toHaveBeenCalledWith('s1'));
    expect(screen.queryByText(/summary/i, { selector: 'p' })).not.toBeInTheDocument();
  });

  it('still renders the page when listModels rejects on mount', async () => {
    mocks.listModels.mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByLabelText('Chat message input')).toBeDefined();
    await waitFor(() => expect(mocks.listModels).toHaveBeenCalled());
  });
});

describe('ChatPage — HITL card clears on terminal events', () => {
  it('clears the HITL card once an approval_granted event arrives', async () => {
    renderPage();
    const es = await sendMsg('deploy to prod');
    act(() => {
      es.emit({ type: 'hitl_required', request_id: 'req-1', approval_token: 'tok-1', action: 'Deploy prod' });
    });
    await screen.findByLabelText('Approve action');
    act(() => {
      es.emit({ type: 'approval_granted' });
    });
    await waitFor(() => expect(screen.queryByLabelText('Approve action')).not.toBeInTheDocument());
  });
});

describe('ChatPage — cost badge keydown branch', () => {
  it('ignores unrelated keys and opens the modal on Space', async () => {
    renderPage();
    const es = await sendMsg();
    act(() => {
      es.emit({ type: 'usage', tokens_in: 1, tokens_out: 1, cost_usd: 0.0001 });
      es.emit({ type: 'done' });
    });
    const badge = await screen.findByLabelText('Open session usage');
    fireEvent.keyDown(badge, { key: 'Tab' });
    expect(screen.queryByRole('dialog', { name: 'Session usage' })).not.toBeInTheDocument();
    fireEvent.keyDown(badge, { key: ' ' });
    expect(await screen.findByRole('dialog', { name: 'Session usage' })).toBeDefined();
  });
});
