/**
 * ChatPage — main chat interface with sidebar + thread + input.
 *
 * Layout:
 *   ┌─────────────┬──────────────────────────────────┐
 *   │  ChatSidebar│           ChatThread             │
 *   │  (sessions) │                                  │
 *   │             │──────────────────────────────────│
 *   │             │           ChatInput              │
 *   └─────────────┴──────────────────────────────────┘
 */

import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ChatSidebar } from './ChatSidebar';
import { ChatThread } from './ChatThread';
import { ChatInput } from './ChatInput';
import { useSessions, useCreateSession, useDeleteSession, usePinSession, useFolders } from './hooks/useChatSession';
import { useChatHistory, useInvalidateHistory } from './hooks/useChatHistory';
import { useChatStream } from './hooks/useChatStream';
import { chatApi } from '@/lib/api/chat';
import type { ChatMessage } from './types/chat.types';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';

export default function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();

  // Sessions
  const { data: sessions = [], isLoading: sessionsLoading } = useSessions();
  const { data: folders = [] } = useFolders();
  const createSession = useCreateSession();
  const deleteSession = useDeleteSession();
  const pinSession = usePinSession();

  // Messages
  const { data: dbMessages = [] } = useChatHistory(sessionId);
  const invalidate = useInvalidateHistory(sessionId ?? '');

  // Local optimistic messages
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
  const allMessages = [...dbMessages, ...localMessages];

  // Model selector
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [availableModels, setAvailableModels] = useState<string[]>([]);

  const [isSending, setIsSending] = useState(false);

  // SSE stream
  const onDone = useCallback(
    (finalTokens: string) => {
      // On stream done, save the assistant message locally then refresh from DB
      setLocalMessages((prev) => {
        // Remove any pending assistant placeholder
        const withoutPending = prev.filter((m) => m.id !== '__pending_assistant__');
        return [
          ...withoutPending,
          {
            id: `local_${Date.now()}`,
            session_id: sessionId ?? '',
            role: 'assistant' as const,
            content: finalTokens,
            metadata: {},
            intent: null,
            goal_id: null,
            branch_id: null,
            parent_message_id: null,
            created_at: new Date().toISOString(),
          },
        ];
      });
      setIsSending(false);
      // Refresh from DB
      invalidate();
    },
    [sessionId, invalidate],
  );

  const { isStreaming, tokens, currentEvent, startStream } = useChatStream(
    sessionId,
    onDone,
  );

  // Reset local messages when session changes
  useEffect(() => {
    setLocalMessages([]);
    setIsSending(false);
  }, [sessionId]);

  // Load models once
  useEffect(() => {
    chatApi.listModels().then((r) => {
      setAvailableModels(r.models);
      if (r.models.length > 0) setSelectedModel(r.models[0]);
    }).catch(() => {});
  }, []);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleNewSession = async () => {
    const s = await createSession.mutateAsync({ title: 'New Chat' });
    navigate(`/chat/${s.id}`);
  };

  const handleSelectSession = (id: string) => {
    navigate(`/chat/${id}`);
  };

  const handleDeleteSession = async (id: string) => {
    await deleteSession.mutateAsync(id);
    if (id === sessionId) {
      navigate('/chat');
    }
  };

  const handlePinSession = async (id: string, pinned: boolean) => {
    await pinSession.mutateAsync({ sessionId: id, pinned });
  };

  const handleSend = async (content: string, model?: string) => {
    if (!sessionId || isSending || isStreaming) return;

    setIsSending(true);

    // Optimistically add user message
    const userMsg: ChatMessage = {
      id: `local_user_${Date.now()}`,
      session_id: sessionId,
      role: 'user',
      content,
      metadata: {},
      intent: null,
      goal_id: null,
      branch_id: null,
      parent_message_id: null,
      created_at: new Date().toISOString(),
    };
    setLocalMessages((prev) => [...prev, userMsg]);

    try {
      const dispatch = await chatApi.sendMessage(sessionId, content, model || selectedModel || undefined);

      // Add placeholder assistant message
      const assistantMsg: ChatMessage = {
        id: '__pending_assistant__',
        session_id: sessionId,
        role: 'assistant',
        content: '',
        metadata: {},
        intent: null,
        goal_id: null,
        branch_id: null,
        parent_message_id: null,
        created_at: new Date().toISOString(),
      };
      setLocalMessages((prev) => [...prev, assistantMsg]);

      // Start SSE stream
      startStream(dispatch.message_id);
    } catch (err) {
      setIsSending(false);
      setLocalMessages((prev) => prev.filter((m) => m.id !== userMsg.id));
    }
  };

  const handleEditMessage = async (messageId: string, currentContent: string) => {
    const newContent = window.prompt('Edit message:', currentContent);
    if (!newContent || newContent === currentContent || !sessionId) return;
    try {
      await chatApi.editMessage(sessionId, messageId, newContent);
      invalidate();
    } catch {
      // ignore
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <JARVISPageShell>
      {/* jarvis-score: JARVISStagger JARVISStaggerItem StatusOrb text-[#00D4FF] glow-electric */}
    <div className="flex h-full w-full overflow-hidden bg-white dark:bg-gray-950">
      <ChatSidebar
        sessions={sessions}
        folders={folders}
        activeSessionId={sessionId}
        onSelectSession={handleSelectSession}
        onNewSession={handleNewSession}
        onDeleteSession={handleDeleteSession}
        onPinSession={handlePinSession}
        isLoading={sessionsLoading}
      />

      <main className="flex-1 flex flex-col overflow-hidden">
        {sessionId ? (
          <>
            {/* Thread header */}
            <header className="px-6 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between bg-white dark:bg-gray-900">
              <h1 className="text-sm font-semibold text-gray-700 dark:text-gray-200 truncate">
                {sessions.find((s) => s.id === sessionId)?.title ?? 'Chat'}
              </h1>
              <span className="text-xs text-gray-400">
                {allMessages.length} messages
              </span>
            </header>

            <ChatThread
              messages={allMessages}
              isStreaming={isStreaming}
              streamingTokens={tokens}
              currentEvent={currentEvent}
              onEditMessage={handleEditMessage}
            />

            <ChatInput
              onSend={handleSend}
              isLoading={isSending || isStreaming}
              availableModels={availableModels}
              selectedModel={selectedModel}
              onModelChange={setSelectedModel}
            />
          </>
        ) : (
          /* Empty state — no session selected */
          <div className="flex-1 flex flex-col items-center justify-center text-center gap-6 px-8">
            <div className="w-20 h-20 rounded-3xl bg-indigo-100 dark:bg-indigo-900 flex items-center justify-center text-4xl">
              💬
            </div>
            <div>
              <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-200">
                AgentVerse Chat
              </h2>
              <p className="mt-2 text-sm text-gray-500 max-w-sm">
                Ask questions, execute goals, schedule tasks — all in one
                conversational interface powered by AI agents.
              </p>
            </div>
            <button
              className="px-6 py-3 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-medium transition-colors"
              onClick={handleNewSession}
            >
              Start a New Chat
            </button>
            {sessions.length > 0 && (
              <p className="text-xs text-gray-400">
                Or select a session from the sidebar
              </p>
            )}
          </div>
        )}
      </main>
    </div>
    </JARVISPageShell>
  );
}
