/**
 * Coverage-focused tests for ChatPage: send failure, attachment upload
 * failure, SSE-driven error banner / HITL card / artifact panel, stop
 * generation mid-stream, session delete/pin, slash commands, and the
 * empty-state "select a session" hint.
 *
 * SSE-driven scenarios reuse the MockES pattern from ChatPage.phase7.test.tsx
 * (drive the real useChatStream hook through a fake EventSource) rather than
 * mocking the hook module per-test, to avoid vi.resetModules()/dynamic
 * re-imports which are expensive in this memory-constrained environment.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ChatPage from './ChatPage';
import { useToastStore } from '@/stores/toast';

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
  uploadAttachment: vi.fn(),
  editMessage: vi.fn(() => Promise.resolve({ message: {}, pruned_message_ids: [] })),
  deleteSession: vi.fn(() => Promise.resolve()),
  pinSession: vi.fn(() => Promise.resolve({})),
  listArtifacts: vi.fn(() =>
    Promise.resolve({
      artifacts: [{ id: 'art1', session_id: 's1', title: 'Report', language: 'markdown', content: '# hi', created_at: new Date().toISOString() }],
    }),
  ),
  artifactDownloadUrl: vi.fn(() => 'http://test/artifact/art1'),
  createSession: vi.fn(() => Promise.resolve({ ...baseSession, id: 'new-session-1', title: 'New Chat' })),
  approve: vi.fn(() => Promise.resolve({ status: 'approved' })),
  reject: vi.fn(() => Promise.resolve({ status: 'rejected' })),
}));

vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    listSessions: () => Promise.resolve({ sessions: [baseSession] }),
    listFolders: () => Promise.resolve({ folders: [] }),
    listMessages: () => Promise.resolve({ messages: [] }),
    listModels: () => Promise.resolve({ models: ['gpt-4o'] }),
    listArtifacts: mocks.listArtifacts,
    artifactDownloadUrl: mocks.artifactDownloadUrl,
    sendMessage: mocks.sendMessage,
    editMessage: mocks.editMessage,
    uploadAttachment: mocks.uploadAttachment,
    deleteSession: mocks.deleteSession,
    pinSession: mocks.pinSession,
    createSession: mocks.createSession,
    streamUrl: () => 'http://test/stream',
  },
}));

vi.mock('@/lib/api/client', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('@/lib/api/client');
  return {
    ...actual,
    governanceApi: {
      approve: mocks.approve,
      reject: mocks.reject,
    },
  };
});

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// Capture EventSource instances so tests can push SSE events (same pattern as
// ChatPage.phase7.test.tsx).
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
  mocks.uploadAttachment.mockReset();
  mocks.editMessage.mockClear();
  mocks.deleteSession.mockClear();
  mocks.pinSession.mockClear();
  mocks.listArtifacts.mockClear();
  mocks.createSession.mockClear();
  mocks.approve.mockClear();
  mocks.reject.mockClear();
  useToastStore.setState({ toasts: [] });
  MockES.instances = [];
  vi.stubGlobal('EventSource', MockES as unknown as typeof EventSource);
});

describe('ChatPage — send failure', () => {
  it('rolls back the optimistic user message and clears isSending when sendMessage rejects', async () => {
    mocks.sendMessage.mockReset();
    mocks.sendMessage.mockRejectedValue(new Error('network down'));
    renderPage();
    const input = await screen.findByLabelText('Chat message input');
    await userEvent.type(input, 'hello there');
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalled());
    // The optimistic message should be rolled back — it should not remain in the thread.
    await waitFor(() => expect(screen.queryByText('hello there')).not.toBeInTheDocument());
    // No stream was ever started.
    expect(MockES.instances.length).toBe(0);
  });
});

describe('ChatPage — attachment upload failure', () => {
  it('propagates an upload failure so no attachment chip is created', async () => {
    mocks.uploadAttachment.mockRejectedValue(new Error('file too large'));
    renderPage();
    const fileInput = await screen.findByTestId('file-input');
    const file = new File(['x'.repeat(10)], 'big.pdf', { type: 'application/pdf' });
    await userEvent.upload(fileInput, file);

    await waitFor(() => expect(mocks.uploadAttachment).toHaveBeenCalledWith('s1', file));
    expect(screen.queryByText('big.pdf')).not.toBeInTheDocument();
  });
});

describe('ChatPage — SSE error banner', () => {
  it('shows the error banner on a server error event and retries the last user message on click', async () => {
    renderPage();
    const es = await sendMsg('please deploy');
    act(() => {
      es.emit({ type: 'error', message: 'Connection lost' });
    });
    expect(await screen.findByText('Connection lost')).toBeDefined();

    mocks.sendMessage.mockClear();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    await waitFor(() => expect(mocks.sendMessage).toHaveBeenCalled());
    const [, content] = mocks.sendMessage.mock.calls[0] as unknown as [string, string];
    expect(content).toBe('please deploy');
  });
});

describe('ChatPage — stop generation mid-stream', () => {
  it('closes the EventSource when the stop button is clicked while streaming', async () => {
    renderPage();
    const es = await sendMsg();
    const stopBtn = await screen.findByTestId('stop-button');
    fireEvent.click(stopBtn);
    expect(es.closed).toBe(true);
  });
});

describe('ChatPage — HITL approve/reject', () => {
  async function emitHitl() {
    renderPage();
    const es = await sendMsg('deploy to prod');
    act(() => {
      es.emit({
        type: 'hitl_required',
        request_id: 'req-1',
        approval_token: 'tok-1',
        action: 'Deploy prod',
        risk_level: 'high',
        timeout_seconds: 120,
      });
    });
    // "Deploy prod" also appears in the AgenticExecutionPanel's event timeline
    // while streaming, so just wait for the HITL card's own approve control.
    await screen.findByLabelText('Approve action');
  }

  it('approves via governanceApi and toasts success', async () => {
    await emitHitl();
    fireEvent.click(screen.getByLabelText('Approve action'));
    await waitFor(() => expect(mocks.approve).toHaveBeenCalledWith('req-1', 'chat-user', 'Approved from chat'));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'success' && t.message.includes('Approved'))).toBe(true);
    });
  });

  it('surfaces a toast when approve fails', async () => {
    mocks.approve.mockRejectedValueOnce(new Error('boom'));
    await emitHitl();
    fireEvent.click(screen.getByLabelText('Approve action'));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Approve failed'))).toBe(true);
    });
  });

  it('rejects via governanceApi and toasts the rejection', async () => {
    await emitHitl();
    fireEvent.click(screen.getByLabelText('Reject action'));
    await waitFor(() => expect(mocks.reject).toHaveBeenCalledWith('req-1', 'chat-user', 'Rejected from chat'));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Rejected'))).toBe(true);
    });
  });

  it('surfaces a toast when reject fails', async () => {
    mocks.reject.mockRejectedValueOnce(new Error('boom'));
    await emitHitl();
    fireEvent.click(screen.getByLabelText('Reject action'));
    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.kind === 'error' && t.message.includes('Reject failed'))).toBe(true);
    });
  });
});

describe('ChatPage — artifacts panel', () => {
  it('opens and closes the artifact panel from an artifact_created event', async () => {
    renderPage();
    const es = await sendMsg('generate a report');
    act(() => {
      es.emit({ type: 'artifact_created', artifact_id: 'art1', title: 'Report', language: 'markdown' });
    });

    const openBtn = await screen.findByLabelText('Open Report');
    fireEvent.click(openBtn);
    await waitFor(() => expect(mocks.listArtifacts).toHaveBeenCalledWith('s1'));
    expect(await screen.findByLabelText('Artifact panel')).toBeDefined();

    fireEvent.click(screen.getByLabelText('Close artifact panel'));
    await waitFor(() => expect(screen.queryByLabelText('Artifact panel')).not.toBeInTheDocument());
  });
});

describe('ChatPage — session list actions', () => {
  it('deletes the active session and navigates to /chat', async () => {
    renderPage();
    await screen.findByTestId('session-s1');
    fireEvent.click(screen.getByLabelText('Delete session')); // arm
    fireEvent.click(screen.getByLabelText('Confirm delete session')); // confirm
    await waitFor(() => expect(mocks.deleteSession).toHaveBeenCalledWith('s1'));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/chat'));
  });

  it('pins a session via the pin button', async () => {
    renderPage();
    await screen.findByTestId('session-s1');
    fireEvent.click(screen.getByLabelText('Pin session'));
    await waitFor(() => expect(mocks.pinSession).toHaveBeenCalledWith('s1', true));
  });
});

describe('ChatPage — slash command', () => {
  it('/clear creates a new session and navigates to it', async () => {
    renderPage();
    const input = await screen.findByLabelText('Chat message input');
    await userEvent.type(input, '/clear');
    const menu = await screen.findByTestId('slash-menu');
    fireEvent.click(within(menu).getByRole('option', { name: /\/clear/ }));
    await waitFor(() => expect(mocks.createSession).toHaveBeenCalled());
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/chat/new-session-1'));
    // A slash command never hits the normal send path.
    expect(mocks.sendMessage).not.toHaveBeenCalled();
  });
});

describe('ChatPage — empty state with existing sessions', () => {
  it('shows the "select a session from the sidebar" hint when sessions exist', async () => {
    renderPage('/chat');
    expect(await screen.findByText('Or select a session from the sidebar')).toBeDefined();
  });
});
