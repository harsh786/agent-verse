/**
 * Unit tests for ChatPage component.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ChatPage from './ChatPage';

// Mock chatApi
vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    listSessions: () => Promise.resolve({ sessions: [] }),
    listFolders: () => Promise.resolve({ folders: [] }),
    listMessages: () => Promise.resolve({ messages: [] }),
    listModels: () => Promise.resolve({ models: ['gpt-4o', 'claude-3-5-sonnet'] }),
    createSession: () =>
      Promise.resolve({
        id: 'new-session-1',
        title: 'New Chat',
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
        tenant_id: 't1',
      }),
    sendMessage: () =>
      Promise.resolve({ intent: 'QA', message_id: 'm1', session_id: 's1', clarify_request: null, schedule_confirmation: null }),
    streamUrl: () => 'http://test/stream',
  },
}));

// Mock react-router-dom navigate
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderPage(path = '/chat') {
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

describe('ChatPage', () => {
  beforeEach(() => {
    mockNavigate.mockClear();
  });

  it('renders empty state when no session is selected', () => {
    renderPage('/chat');
    expect(screen.getByText('AgentVerse Chat')).toBeDefined();
  });

  it('renders "Start a New Chat" button in empty state', () => {
    renderPage('/chat');
    expect(screen.getByText('Start a New Chat')).toBeDefined();
  });

  it('navigates to new session on "Start a New Chat" click', async () => {
    renderPage('/chat');
    const btn = screen.getByText('Start a New Chat');
    fireEvent.click(btn);
    // Allow async
    await new Promise((r) => setTimeout(r, 100));
    expect(mockNavigate).toHaveBeenCalledWith('/chat/new-session-1');
  });

  it('shows sidebar with "New Chat" button', () => {
    renderPage('/chat');
    expect(screen.getByTestId('new-chat-button')).toBeDefined();
  });

  it('renders chat input area when session is active', () => {
    renderPage('/chat/test-session-id');
    // Input should be visible
    expect(screen.getByLabelText('Chat message input')).toBeDefined();
  });

  it('shows send button disabled when input is empty', () => {
    renderPage('/chat/test-session-id');
    const sendBtn = screen.getByTestId('send-button');
    expect(sendBtn).toHaveProperty('disabled', true);
  });
});
