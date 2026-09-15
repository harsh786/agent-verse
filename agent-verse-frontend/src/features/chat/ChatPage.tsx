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
import { Settings, Plug, FileText, BarChart3 } from 'lucide-react';
import { ChatSidebar } from './ChatSidebar';
import { ChatThread } from './ChatThread';
import { ChatInput, type AttachmentChip } from './ChatInput';
import { ChatHITLCard } from './ChatHITLCard';
import { ChatErrorBanner } from './ChatErrorBanner';
import { ChatReasoningPanel } from './ChatReasoningPanel';
import { ChatArtifactCard, type ArtifactCardData } from './ChatArtifactCard';
import { ChatArtifactPanel } from './ChatArtifactPanel';
import { ChatModelSelector } from './ChatModelSelector';
import { ChatTokenCostBadge } from './ChatTokenCostBadge';
import { ChatSessionSettingsModal } from './ChatSessionSettingsModal';
import { ChatClarifyCard } from './ChatClarifyCard';
import { ChatGoalFailureCard } from './ChatGoalFailureCard';
import { ChatGoalSummary } from './ChatGoalSummary';
import { ChatConversationSummary } from './ChatConversationSummary';
import { ConnectedServicesPanel } from './ConnectedServicesPanel';
import { ChatUsageModal } from './ChatUsageModal';
import { ChatScheduleCard } from './ChatScheduleCard';
import { mergeChatMessages } from './mergeMessages';
import { useSessions, useCreateSession, useDeleteSession, usePinSession, useRenameSession, useUpdateSession, useFolders } from './hooks/useChatSession';
import { useChatHistory, useInvalidateHistory } from './hooks/useChatHistory';
import { useChatStream } from './hooks/useChatStream';
import { chatApi } from '@/lib/api/chat';
import { governanceApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import type { ChatMessage, ChatArtifact, ChatUsageSummary, SSEEvent, UpdateSessionPayload } from './types/chat.types';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';
import { AgenticExecutionPanel } from './components/AgenticExecutionPanel';

// ── SSE event → card-data helpers ────────────────────────────────────────────

interface ScheduleCardData {
  key: string;
  humanSchedule?: string;
  cronExpression?: string;
  nextRunIso?: string | null;
  goalText?: string;
}

interface CostInfo {
  tokensIn: number;
  tokensOut: number;
  costUsd: number;
  model?: string;
}

function num(...vals: unknown[]): number {
  for (const v of vals) {
    if (typeof v === 'number' && !Number.isNaN(v)) return v;
  }
  return 0;
}

function suggestionsOf(e: SSEEvent | undefined): string[] {
  if (!e) return [];
  const raw = (e.suggestions ?? e.next_steps ?? e.follow_ups) as unknown;
  return Array.isArray(raw) ? raw.map((s) => String(s)) : [];
}

function toScheduleCard(e: SSEEvent): ScheduleCardData {
  const cron = e.cron_expression ? String(e.cron_expression) : undefined;
  const human = e.human_schedule ? String(e.human_schedule) : undefined;
  const nextRun = (e.next_run_iso ?? e.next_run) as string | null | undefined;
  return {
    key: cron ?? human ?? JSON.stringify(e),
    cronExpression: cron,
    humanSchedule: human,
    nextRunIso: nextRun ?? null,
    goalText: e.goal_text ? String(e.goal_text) : undefined,
  };
}

function toCostInfo(e: SSEEvent): CostInfo {
  return {
    tokensIn: num(e.tokens_in, e.input_tokens, e.prompt_tokens),
    tokensOut: num(e.tokens_out, e.output_tokens, e.completion_tokens),
    costUsd: num(e.cost_usd, e.cost),
    model: e.model ? String(e.model) : undefined,
  };
}

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
  const updateSession = useUpdateSession(sessionId ?? '');

  // Messages
  const { data: dbMessages = [] } = useChatHistory(sessionId);
  const invalidate = useInvalidateHistory(sessionId ?? '');

  // Local optimistic messages, de-duplicated against the persisted DB copies so
  // a streamed reply (or optimistic user turn) is not shown twice after refetch.
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
  const allMessages = mergeChatMessages(dbMessages, localMessages);

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

  // Phase 7 — schedule/channel awareness + orphan-card state, derived from the
  // structural SSE event stream (and the send-dispatch result).
  const [scheduleCards, setScheduleCards] = useState<ScheduleCardData[]>([]);
  const [costInfo, setCostInfo] = useState<CostInfo | null>(null);
  const [goalSummary, setGoalSummary] = useState<{ summary: string; suggestions: string[] } | null>(null);
  const [failure, setFailure] = useState<{ reason: string; suggestions: string[] } | null>(null);
  const [clarify, setClarify] = useState<{ question: string; options: string[]; round: number } | null>(null);

  // Header-driven panels/modals.
  const [showSettings, setShowSettings] = useState(false);
  const [showServices, setShowServices] = useState(false);
  const [showUsage, setShowUsage] = useState(false);
  const [usageSummary, setUsageSummary] = useState<ChatUsageSummary | null>(null);
  const [conversationSummary, setConversationSummary] = useState<string | null>(null);

  // Derive the Phase-7 cards from the streamed structural events. Values are
  // only SET when a matching event is present; they are cleared explicitly when
  // a new turn is sent (below) so dispatch-provided data survives the stream.
  useEffect(() => {
    const evs = streamEvents as SSEEvent[];
    const scheds = evs.filter((e) => e.type === 'schedule_created');
    if (scheds.length > 0) {
      setScheduleCards((prev) => {
        const next = [...prev];
        for (const e of scheds) {
          const card = toScheduleCard(e);
          if (!next.some((c) => c.key === card.key)) next.push(card);
        }
        return next;
      });
    }

    const lastCost = [...evs].reverse().find((e) => e.type === 'usage' || e.type === 'cost');
    if (lastCost) setCostInfo(toCostInfo(lastCost));

    const goal = [...evs].reverse().find((e) => e.type === 'goal_complete');
    if (goal) {
      const proactive = [...evs].reverse().find((e) => e.type === 'proactive_suggestions');
      const suggestions = suggestionsOf(goal).length ? suggestionsOf(goal) : suggestionsOf(proactive);
      setGoalSummary({
        summary: String(goal.summary ?? goal.result ?? 'Goal completed successfully.'),
        suggestions,
      });
    }

    const fail = [...evs].reverse().find(
      (e) => e.type === 'failure_analysis' || (e.type === 'error' && (e.reason || e.analysis)),
    );
    if (fail) {
      setFailure({
        reason: String(fail.reason ?? fail.analysis ?? fail.message ?? 'The goal could not be completed.'),
        suggestions: suggestionsOf(fail),
      });
    }

    const clar = [...evs].reverse().find((e) => e.type === 'clarify_needed');
    if (clar) {
      setClarify({
        question: String(clar.question ?? ''),
        options: Array.isArray(clar.options) ? (clar.options as string[]) : [],
        round: Number(clar.round ?? 1),
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    setScheduleCards([]);
    setCostInfo(null);
    setGoalSummary(null);
    setFailure(null);
    setClarify(null);
    setConversationSummary(null);
    setShowSettings(false);
    setShowServices(false);
    setShowUsage(false);
    setUsageSummary(null);
  }, [sessionId]);

  // Load models once; prefer the session's saved model when present.
  const activeSession = sessions.find((s) => s.id === sessionId) ?? null;
  useEffect(() => {
    chatApi.listModels().then((r) => {
      setAvailableModels(r.models);
      setSelectedModel((cur) => cur || activeSession?.preferred_model || r.models[0] || '');
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Adopt the session's preferred model when switching sessions.
  useEffect(() => {
    if (activeSession?.preferred_model) setSelectedModel(activeSession.preferred_model);
  }, [activeSession?.preferred_model]);

  // Persist a model change to the session (reuses the session-update path).
  const handleModelChange = useCallback(
    (model: string) => {
      setSelectedModel(model);
      if (sessionId) updateSession.mutate({ preferred_model: model });
    },
    [sessionId, updateSession],
  );

  // Session settings modal → persist rename + settings via the update path.
  const handleSaveSettings = useCallback(
    (updates: UpdateSessionPayload) => {
      if (!sessionId) return;
      updateSession.mutate(updates);
    },
    [sessionId, updateSession],
  );

  // Usage modal — fetch the session usage summary on open.
  const handleOpenUsage = useCallback(async () => {
    if (!sessionId) return;
    setShowUsage(true);
    try {
      setUsageSummary(await chatApi.getUsage(sessionId));
    } catch {
      /* ignore — modal shows nothing until data loads */
    }
  }, [sessionId]);

  // Conversation summary card (from /summarize).
  const handleSummarize = useCallback(async () => {
    if (!sessionId) return;
    try {
      const { summary } = await chatApi.summarizeSession(sessionId);
      setConversationSummary(summary);
    } catch {
      /* ignore */
    }
  }, [sessionId]);

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
    // Clear the previous turn's transient outcome cards (they re-derive from the
    // new stream). Schedules stay for the whole session.
    setClarify(null);
    setFailure(null);
    setGoalSummary(null);

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

      // Non-streaming dispatch outcomes (clarify / schedule) come back on the
      // POST itself; surface them immediately so they survive the stream reset.
      if (dispatch.clarify_request) {
        setClarify({
          question: dispatch.clarify_request.question,
          options: dispatch.clarify_request.options ?? [],
          round: dispatch.clarify_request.round ?? 1,
        });
      }
      if (dispatch.schedule_confirmation) {
        const sc = dispatch.schedule_confirmation;
        const card: ScheduleCardData = {
          key: sc.cron_expression || sc.human_schedule || sc.goal_text,
          cronExpression: sc.cron_expression || undefined,
          humanSchedule: sc.human_schedule || undefined,
          nextRunIso: sc.next_run_iso,
          goalText: sc.goal_text || undefined,
        };
        setScheduleCards((prev) => (prev.some((c) => c.key === card.key) ? prev : [...prev, card]));
      }

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
    <JARVISStagger className="flex h-full w-full overflow-hidden bg-card">
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
            className="w-64 shrink-0 border-r border-border rounded-none"
          />
        )}
        <div className="flex-1 flex flex-col overflow-hidden">
        {sessionId ? (
          <>
            {/* Thread header */}
            <header className="px-6 py-3 border-b border-border flex items-center justify-between gap-3 bg-card">
              <h1 className="text-sm font-semibold text-muted-foreground truncate min-w-0">
                {activeSession?.title ?? 'Chat'}
              </h1>
              <div className="flex items-center gap-2 shrink-0">
                {costInfo && (
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label="Open session usage"
                    className="cursor-pointer"
                    onClick={() => void handleOpenUsage()}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') void handleOpenUsage(); }}
                  >
                    <ChatTokenCostBadge
                      tokensIn={costInfo.tokensIn}
                      tokensOut={costInfo.tokensOut}
                      costUsd={costInfo.costUsd}
                      model={costInfo.model}
                    />
                  </span>
                )}
                {availableModels.length > 0 && (
                  <ChatModelSelector models={availableModels} selected={selectedModel} onChange={handleModelChange} />
                )}
                <button
                  className="p-1.5 rounded-lg text-muted-foreground/70 hover:text-muted-foreground hover:bg-white/[0.05] transition-colors"
                  aria-label="Session usage"
                  onClick={() => void handleOpenUsage()}
                >
                  <BarChart3 className="w-4 h-4" />
                </button>
                <button
                  className="p-1.5 rounded-lg text-muted-foreground/70 hover:text-muted-foreground hover:bg-white/[0.05] transition-colors"
                  aria-label="Summarize conversation"
                  onClick={() => void handleSummarize()}
                >
                  <FileText className="w-4 h-4" />
                </button>
                <button
                  className={`p-1.5 rounded-lg transition-colors ${showServices ? 'text-indigo-400 bg-white/[0.06]' : 'text-muted-foreground/70 hover:text-muted-foreground hover:bg-white/[0.05]'}`}
                  aria-label="Connected services"
                  aria-pressed={showServices}
                  onClick={() => setShowServices((v) => !v)}
                >
                  <Plug className="w-4 h-4" />
                </button>
                <button
                  className="p-1.5 rounded-lg text-muted-foreground/70 hover:text-muted-foreground hover:bg-white/[0.05] transition-colors"
                  aria-label="Session settings"
                  onClick={() => setShowSettings(true)}
                >
                  <Settings className="w-4 h-4" />
                </button>
                <span className="text-xs text-muted-foreground ml-1">{allMessages.length} messages</span>
              </div>
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
            {/* Schedule-confirmation cards (schedule_created / dispatch) */}
            {scheduleCards.length > 0 && (
              <div className="mx-4 mb-2 flex flex-col gap-2" aria-label="Created schedules">
                {scheduleCards.map((s) => (
                  <ChatScheduleCard
                    key={s.key}
                    humanSchedule={s.humanSchedule}
                    cronExpression={s.cronExpression}
                    nextRunIso={s.nextRunIso}
                    goalText={s.goalText}
                  />
                ))}
              </div>
            )}
            {/* Clarification request (clarify_needed / dispatch clarify_request) */}
            {clarify && (
              <div className="mx-4">
                <ChatClarifyCard
                  question={clarify.question}
                  options={clarify.options}
                  round={clarify.round}
                  onAnswer={(answer) => { setClarify(null); void handleSend(answer); }}
                />
              </div>
            )}
            {/* Goal completion summary (goal_complete) */}
            {goalSummary && (
              <div className="mx-4">
                <ChatGoalSummary
                  summary={goalSummary.summary}
                  suggestions={goalSummary.suggestions}
                  onSuggestionClick={(text) => void handleSend(text)}
                />
              </div>
            )}
            {/* Conversation summary card (/summarize) */}
            {conversationSummary && (
              <div className="mx-4">
                <ChatConversationSummary summary={conversationSummary} />
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
            {/* Goal failure analysis card takes precedence over the raw error banner. */}
            {failure && !isStreaming ? (
              <div className="mx-4">
                <ChatGoalFailureCard
                  reason={failure.reason}
                  suggestions={failure.suggestions}
                  onRetry={(s) => void handleSend(s)}
                />
              </div>
            ) : (
              streamError && !isStreaming && (
                <ChatErrorBanner
                  message={streamError}
                  onRetry={() => {
                    const lastUser = [...allMessages].reverse().find((m) => m.role === 'user');
                    if (lastUser) void handleSend(lastUser.content);
                  }}
                />
              )
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
              <h2 className="text-xl font-semibold text-foreground">
                AgentVerse Chat
              </h2>
              <p className="mt-2 text-sm text-muted-foreground/70 max-w-sm">
                Ask questions, execute goals, schedule tasks — all in one
                conversational interface powered by AI agents.
              </p>
            </div>
            <button
              className="px-6 py-3 bg-indigo-600 hover:bg-indigo-700 text-foreground rounded-xl font-medium transition-colors"
              onClick={handleNewSession}
            >
              Start a New Chat
            </button>
            {sessions.length > 0 && (
              <p className="text-xs text-muted-foreground">
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
        {/* Connected MCP services side panel (header toggle) */}
        {showServices && (
          <aside className="w-80 shrink-0 border-l border-border bg-card">
            <ConnectedServicesPanel onClose={() => setShowServices(false)} />
          </aside>
        )}
      </main>

      {/* Session settings modal (header cog) */}
      {showSettings && (
        <ChatSessionSettingsModal
          session={activeSession}
          onClose={() => setShowSettings(false)}
          onSave={handleSaveSettings}
        />
      )}

      {/* Session usage modal (token badge / usage button) */}
      {showUsage && (
        <ChatUsageModal summary={usageSummary} onClose={() => { setShowUsage(false); setUsageSummary(null); }} />
      )}
    </JARVISStagger>
    </JARVISPageShell>
  );
}
