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
import { ChatInput, type AttachmentChip } from './ChatInput';
import { ChatHITLCard } from './ChatHITLCard';
import { ChatErrorBanner } from './ChatErrorBanner';
import { ChatReasoningPanel } from './ChatReasoningPanel';
import { ChatArtifactCard, type ArtifactCardData } from './ChatArtifactCard';
import { ChatArtifactPanel } from './ChatArtifactPanel';
import { useSessions, useCreateSession, useDeleteSession, usePinSession, useRenameSession, useFolders } from './hooks/useChatSession';
import { useChatHistory, useInvalidateHistory } from './hooks/useChatHistory';
import { useChatStream } from './hooks/useChatStream';
import { chatApi } from '@/lib/api/chat';
import { governanceApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import type { ChatMessage, ChatArtifact, SSEEvent } from './types/chat.types';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';
import { AgenticExecutionPanel } from './components/AgenticExecutionPanel';

export default function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();

  // Sessions
  const { data: sessions = [], isLoading: sessionsLoading } = useSessions();
  const { data: folders = [] } = useFolders();
  const createSession = useCreateSession();
  const deleteSession = useDeleteSession();
  const pinSession = usePinSession();
  const renameSession = useRenameSession();

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

  const { isStreaming, tokens, reasoning, currentEvent, events: streamEvents, startStream, stopStream, error: streamError } = useChatStream(
    sessionId,
    onDone,
  );

  // Downloadable artifacts produced during the session. streamEvents reset on
  // each new stream, so accumulate artifact_created events into session state.
  const [artifacts, setArtifacts] = useState<ArtifactCardData[]>([]);
  const [openArtifact, setOpenArtifact] = useState<ChatArtifact | null>(null);

  useEffect(() => {
    const created = (streamEvents as SSEEvent[]).filter((e) => e.type === 'artifact_created');
    if (created.length === 0) return;
    setArtifacts((prev) => {
      const next = [...prev];
      for (const e of created) {
        const id = String(e.artifact_id ?? '');
        if (!id || next.some((a) => a.artifactId === id)) continue;
        next.push({ artifactId: id, title: String(e.title ?? 'Artifact'), language: e.language ? String(e.language) : undefined });
      }
      return next;
    });
  }, [streamEvents]);

  const handleOpenArtifact = useCallback(
    async (artifactId: string) => {
      if (!sessionId) return;
      try {
        const { artifacts: list } = await chatApi.listArtifacts(sessionId);
        const full = list.find((a) => a.id === artifactId);
        if (full) setOpenArtifact(full);
      } catch {
        /* ignore — download still available from the card */
      }
    },
    [sessionId],
  );

  // Reset local + session state when session changes
  useEffect(() => {
    setLocalMessages([]);
    setIsSending(false);
    setArtifacts([]);
    setOpenArtifact(null);
  }, [sessionId]);

  // Load models once
  useEffect(() => {
    chatApi.listModels().then((r) => {
      setAvailableModels(r.models);
      if (r.models.length > 0) setSelectedModel(r.models[0]);
    }).catch(() => {});
  }, []);

  // G-02: Surface hitl_required events from the chat stream as an inline
  // approval card. When the backend emits hitl_required we capture the
  // latest event in local state so ChatHITLCard can render with the right
  // requestId/approvalToken, and we wire approve/reject to governanceApi.
  const [hitlEvent, setHitlEvent] = useState<any>(null); // eslint-disable-line @typescript-eslint/no-explicit-any
  useEffect(() => {
    if (currentEvent && (currentEvent as any).type === 'hitl_required') { // eslint-disable-line @typescript-eslint/no-explicit-any
      setHitlEvent(currentEvent);
    } else if (currentEvent && ['done', 'error', 'approval_granted', 'hitl_rejected'].includes((currentEvent as any).type)) { // eslint-disable-line @typescript-eslint/no-explicit-any
      setHitlEvent(null);
    }
  }, [currentEvent]);

  const handleHITLApprove = useCallback(async () => {
    const reqId = hitlEvent?.request_id as string | undefined;
    if (!reqId) return;
    try {
      await governanceApi.approve(reqId, 'chat-user', 'Approved from chat');
      toast({ kind: 'success', message: 'Approved — the action was approved from chat.' });
      setHitlEvent(null);
    } catch (err) {
      toast({ kind: 'error', message: `Approve failed: ${err}` });
    }
  }, [hitlEvent]);

  const handleHITLReject = useCallback(async () => {
    const reqId = hitlEvent?.request_id as string | undefined;
    if (!reqId) return;
    try {
      await governanceApi.reject(reqId, 'chat-user', 'Rejected from chat');
      toast({ kind: 'error', message: 'Rejected — the action was rejected from chat.' });
      setHitlEvent(null);
    } catch (err) {
      toast({ kind: 'error', message: `Reject failed: ${err}` });
    }
  }, [hitlEvent]);

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

  // Inline edit-and-rerun: ChatMessage submits the edited content directly (no
  // window.prompt). PATCH the message, then re-run the turn via the send path.
  const handleEditMessage = async (messageId: string, newContent: string) => {
    if (!sessionId) return;
    try {
      await chatApi.editMessage(sessionId, messageId, newContent);
      invalidate();
      await handleSend(newContent);
    } catch {
      // ignore
    }
  };

  // Regenerate: re-run the last user turn through the existing send path.
  const handleRegenerate = () => {
    const lastUser = [...allMessages].reverse().find((m) => m.role === 'user');
    if (lastUser) void handleSend(lastUser.content);
  };

  const handleUploadAttachment = useCallback(
    async (file: File): Promise<AttachmentChip> => {
      if (!sessionId) throw new Error('No active session');
      const res = await chatApi.uploadAttachment(sessionId, file);
      return {
        attachment_id: res.attachment_id,
        filename: res.filename,
        content_type: res.content_type,
        size: res.size,
      };
    },
    [sessionId],
  );

  const handleSlashCommand = (command: string) => {
    if (command === '/clear') void handleNewSession();
  };

  const canRegenerate = allMessages.some((m) => m.role === 'user');

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <JARVISPageShell>
    <JARVISStagger className="flex h-full w-full overflow-hidden bg-[#0F1826] dark:bg-[#060810]">
      <ChatSidebar
        sessions={sessions}
        folders={folders}
        activeSessionId={sessionId}
        onSelectSession={handleSelectSession}
        onNewSession={handleNewSession}
        onDeleteSession={handleDeleteSession}
        onPinSession={handlePinSession}
        onRenameSession={(id, title) => void renameSession.mutateAsync({ sessionId: id, title })}
        isLoading={sessionsLoading}
      />

      <main className="flex-1 flex overflow-hidden">
        {/* Agentic Execution Panel — slides in when streaming (spec §6) */}
        {isStreaming && (
          <AgenticExecutionPanel
            events={streamEvents as unknown[] as never}
            isActive={isStreaming}
            className="w-64 shrink-0 border-r border-white/[0.06] rounded-none"
          />
        )}
        <div className="flex-1 flex flex-col overflow-hidden">
        {sessionId ? (
          <>
            {/* Thread header */}
            <header className="px-6 py-3 border-b border-white/[0.08] dark:border-[#1E2535] flex items-center justify-between bg-[#0F1826] dark:bg-[#0F1117]">
              <h1 className="text-sm font-semibold text-[#A0B4CC] dark:text-[#E2E8F0] truncate">
                {sessions.find((s) => s.id === sessionId)?.title ?? 'Chat'}
              </h1>
              <span className="text-xs text-[#A0B4CC]">
                {allMessages.length} messages
              </span>
            </header>

            <ChatThread
              messages={allMessages}
              isStreaming={isStreaming}
              streamingTokens={tokens}
              currentEvent={currentEvent}
              onEditMessage={handleEditMessage}
              onSuggestionSelect={(prompt) => void handleSend(prompt)}
            />
            {/* Agent transparency: collapsible reasoning stream */}
            <ChatReasoningPanel reasoning={reasoning} isStreaming={isStreaming} />
            {/* Downloadable artifact cards from artifact_created events */}
            {artifacts.length > 0 && (
              <div className="mx-4 mb-2 flex flex-col gap-2" aria-label="Generated artifacts">
                {artifacts.map((a) => (
                  <ChatArtifactCard key={a.artifactId} artifact={a} onOpen={handleOpenArtifact} />
                ))}
              </div>
            )}
            {hitlEvent && (
              <ChatHITLCard
                stepName={String(hitlEvent.action ?? hitlEvent.step_name ?? 'Pending action')}
                riskLevel={String(hitlEvent.risk_level ?? 'high')}
                timeoutSeconds={Number(hitlEvent.timeout_seconds ?? 300)}
                requestId={hitlEvent.request_id}
                approvalToken={hitlEvent.approval_token}
                onApprove={handleHITLApprove}
                onReject={handleHITLReject}
              />
            )}
            {streamError && !isStreaming && (
              <ChatErrorBanner
                message={streamError}
                onRetry={() => {
                  const lastUser = [...allMessages].reverse().find((m) => m.role === 'user');
                  if (lastUser) void handleSend(lastUser.content);
                }}
              />
            )}
            <ChatInput
              onSend={handleSend}
              isLoading={isSending || isStreaming}
              availableModels={availableModels}
              selectedModel={selectedModel}
              onModelChange={setSelectedModel}
              onStop={isStreaming ? stopStream : undefined}
              onRegenerate={handleRegenerate}
              canRegenerate={canRegenerate && !isSending && !isStreaming}
              onUploadAttachment={handleUploadAttachment}
              onSlashCommand={handleSlashCommand}
            />
          </>
        ) : (
          /* Empty state — no session selected */
          <div className="flex-1 flex flex-col items-center justify-center text-center gap-6 px-8">
            <div className="w-20 h-20 rounded-3xl bg-indigo-100 dark:bg-indigo-900 flex items-center justify-center text-4xl">
              💬
            </div>
            <div>
              <h2 className="text-xl font-semibold text-[#F0F6FF] dark:text-[#E2E8F0]">
                AgentVerse Chat
              </h2>
              <p className="mt-2 text-sm text-[#5A7494] max-w-sm">
                Ask questions, execute goals, schedule tasks — all in one
                conversational interface powered by AI agents.
              </p>
            </div>
            <button
              className="px-6 py-3 bg-indigo-600 hover:bg-indigo-700 text-[#F1F5F9] rounded-xl font-medium transition-colors"
              onClick={handleNewSession}
            >
              Start a New Chat
            </button>
            {sessions.length > 0 && (
              <p className="text-xs text-[#A0B4CC]">
                Or select a session from the sidebar
              </p>
            )}
          </div>
        )}
        </div>{/* end flex-1 flex-col overflow-hidden */}
        {/* Artifact side panel (opened from an artifact card) */}
        {openArtifact && (
          <ChatArtifactPanel artifact={openArtifact} onClose={() => setOpenArtifact(null)} />
        )}
      </main>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
