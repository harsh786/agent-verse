/** Phase 7 — ChatPage composer wiring: regenerate re-sends last user turn;
 *  inline edit calls the edit API (and never window.prompt). */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ChatPage from './ChatPage';

const userMsg = {
  id: 'u1',
  session_id: 'test-session-id',
  role: 'user' as const,
  content: 'hello world',
  metadata: {},
  intent: null,
  goal_id: null,
  branch_id: null,
  parent_message_id: null,
  created_at: new Date().toISOString(),
};

const sendMessage = vi.fn(() =>
  Promise.resolve({ intent: 'QA', message_id: 'm1', session_id: 'test-session-id', clarify_request: null, schedule_confirmation: null }),
);
const editMessage = vi.fn(() => Promise.resolve({ message: userMsg, pruned_message_ids: [] }));
const uploadAttachment = vi.fn(() =>
  Promise.resolve({ attachment_id: 'a1', filename: 'doc.pdf', content_type: 'application/pdf', size: 4 }),
);

vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    listSessions: () => Promise.resolve({ sessions: [] }),
    listFolders: () => Promise.resolve({ folders: [] }),
    listMessages: () => Promise.resolve({ messages: [userMsg] }),
    listModels: () => Promise.resolve({ models: ['gpt-4o'] }),
    listArtifacts: () => Promise.resolve({ artifacts: [] }),
    sendMessage: (...args: unknown[]) => sendMessage(...(args as [])),
    editMessage: (...args: unknown[]) => editMessage(...(args as [])),
    uploadAttachment: (...args: unknown[]) => uploadAttachment(...(args as [])),
  },
}));

// Avoid opening a real EventSource (jsdom lacks one).
vi.mock('./hooks/useChatStream', () => ({
  useChatStream: () => ({
    isStreaming: false,
    tokens: '',
    reasoning: '',
    currentEvent: null,
    events: [],
    startStream: vi.fn(),
    stopStream: vi.fn(),
    error: null,
  }),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/chat/test-session-id']}>
        <Routes>
          <Route path="/chat/:sessionId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ChatPage composer wiring', () => {
  beforeEach(() => {
    sendMessage.mockClear();
    editMessage.mockClear();
    mockNavigate.mockClear();
  });

  it('regenerate re-sends the last user message', async () => {
    renderPage();
    const btn = await screen.findByTestId('regenerate-button');
    fireEvent.click(btn);
    await waitFor(() => expect(sendMessage).toHaveBeenCalled());
    const [, content] = sendMessage.mock.calls[0] as unknown as [string, string];
    expect(content).toBe('hello world');
  });

  it('inline edit calls the edit API and never window.prompt', async () => {
    const promptSpy = vi.spyOn(window, 'prompt');
    renderPage();
    fireEvent.click(await screen.findByLabelText('Edit message'));
    const ta = screen.getByTestId('edit-textarea-u1');
    fireEvent.change(ta, { target: { value: 'edited world' } });
    fireEvent.click(screen.getByTestId('edit-save-u1'));
    await waitFor(() => expect(editMessage).toHaveBeenCalled());
    const [sid, mid, content] = editMessage.mock.calls[0] as unknown as [string, string, string];
    expect(sid).toBe('test-session-id');
    expect(mid).toBe('u1');
    expect(content).toBe('edited world');
    expect(promptSpy).not.toHaveBeenCalled();
  });
});
