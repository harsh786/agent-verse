import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Activity, AlertCircle, ArrowRight, Bot, Brain, CheckCircle,
  Clock, Lightbulb, MessageSquare, Send, Shield, Sparkles,
  Target, ThumbsDown, ThumbsUp, Users, Wifi, WifiOff, XCircle, Zap,
} from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { useCollabSocket } from '@/lib/ws/useCollabSocket';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

// ─── Types ────────────────────────────────────────────────────────────────────

interface CollabSession {
  session_id: string;
  name: string;
  mode: string;
  goal_id?: string | null;
  agent_id?: string | null;
  participants: string[];
  participant_count: number;
  content: string;
  created_at?: string;
  status: string;
}

interface CollabOperation {
  operation_id: string;
  version: number;
  author: string;
  operation: Record<string, unknown>;
  created_at?: string;
}

interface ConsensusResult {
  agreed: boolean;
  summary: string;
  dissenter?: string | null;
}

interface SessionInsights {
  key_decisions: string[];
  action_items: string[];
  open_questions: string[];
  agreement_level: number;
  sentiment: string;
  summary: string;
}

interface SessionMessage {
  type: string;
  sender?: string;
  content?: string;
  operation?: CollabOperation;
  [key: string]: unknown;
}

// ─── Constants ────────────────────────────────────────────────────────────────

type ModeCfg = { label: string; color: string; bg: string; desc: string };

const MODE_CONFIG: Record<string, ModeCfg> = {
  review:     { label: 'Review',     color: 'text-blue-600',   bg: 'bg-blue-100 text-blue-700',   desc: 'Structured code or document review' },
  suggest:    { label: 'Suggest',    color: 'text-green-600',  bg: 'bg-green-100 text-green-700', desc: 'Collaborative suggestions & edits' },
  debate:     { label: 'Debate',     color: 'text-orange-600', bg: 'bg-orange-100 text-orange-700', desc: 'Structured adversarial discussion' },
  brainstorm: { label: 'Brainstorm', color: 'text-purple-600', bg: 'bg-purple-100 text-purple-700', desc: 'Free-form ideation and exploration' },
};

const TEMPLATES = [
  { name: 'Code Review',            mode: 'review',     participants: 'human:lead,agent:reviewer', desc: 'Review PRs and code changes' },
  { name: 'Product Planning',       mode: 'brainstorm', participants: 'human:pm,agent:planner',    desc: 'Plan features and roadmap' },
  { name: 'Architecture Decision',  mode: 'debate',     participants: 'human:architect,agent:critic', desc: 'Debate architectural choices' },
  { name: 'Bug Triage',             mode: 'suggest',    participants: 'human:lead,agent:debugger', desc: 'Triage and prioritize bugs' },
];

const ROUND_TYPES = [
  { value: 'propose',   label: 'Propose',   color: 'bg-blue-100 text-blue-700' },
  { value: 'critique',  label: 'Critique',  color: 'bg-red-100 text-red-700' },
  { value: 'counter',   label: 'Counter',   color: 'bg-orange-100 text-orange-700' },
  { value: 'agree',     label: 'Agree',     color: 'bg-green-100 text-green-700' },
  { value: 'disagree',  label: 'Disagree',  color: 'bg-rose-100 text-rose-700' },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

const hdrs = (apiKey: string) => ({
  'X-API-Key': apiKey,
  'Content-Type': 'application/json',
});

async function apiFetch<T>(apiKey: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: hdrs(apiKey), ...init });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function operationContent(op: CollabOperation): string {
  const p = op.operation;
  if (typeof p.content === 'string') return p.content;
  if (typeof p.text   === 'string') return p.text;
  return JSON.stringify(p);
}

function timeAgo(iso?: string): string {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000)     return 'just now';
  if (diff < 3_600_000)  return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return new Date(iso).toLocaleDateString();
}

function getModeIcon(mode: string, cls = 'h-3.5 w-3.5') {
  switch (mode) {
    case 'review':     return <Shield     className={cls} />;
    case 'suggest':    return <Lightbulb  className={cls} />;
    case 'debate':     return <Zap        className={cls} />;
    case 'brainstorm': return <Brain      className={cls} />;
    default:           return <Activity   className={cls} />;
  }
}

// ─── ModeBadge ────────────────────────────────────────────────────────────────

function ModeBadge({ mode }: { mode: string }) {
  const cfg = MODE_CONFIG[mode] ?? { label: mode, bg: 'bg-muted text-muted-foreground' };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.bg}`}>
      {getModeIcon(mode)}
      {cfg.label}
    </span>
  );
}

// ─── ParticipantPip ───────────────────────────────────────────────────────────

function ParticipantPip({ id, live = false }: { id: string; live?: boolean }) {
  const isAgent = id.startsWith('agent');
  const label   = id.split(':')[1]?.slice(0, 2).toUpperCase() ?? id.slice(0, 2).toUpperCase();
  return (
    <div className="flex items-center gap-1">
      <div className={`h-7 w-7 rounded-full flex items-center justify-center text-xs font-bold text-white ${isAgent ? 'bg-violet-500' : 'bg-sky-500'}`}>
        {label}
      </div>
      <span className="text-xs text-muted-foreground">{id.split(':')[1] ?? id}</span>
      {isAgent && <span className="text-[10px] px-1 rounded bg-violet-100 text-violet-600 font-medium">AI</span>}
      {live && <span className="h-1.5 w-1.5 rounded-full bg-green-400 animate-pulse" />}
    </div>
  );
}

// ─── LiveSessionPanel ─────────────────────────────────────────────────────────

function LiveSessionPanel({
  session,
  apiKey,
  onClose,
}: {
  session: CollabSession;
  apiKey: string;
  onClose: () => void;
}) {
  const qc              = useQueryClient();
  const messagesEndRef  = useRef<HTMLDivElement>(null);
  const [messages,       setMessages]      = useState<SessionMessage[]>([]);
  const [input,          setInput]         = useState('');
  const [draft,          setDraft]         = useState(session.content ?? '');
  const [draftStatus,    setDraftStatus]   = useState<'saved' | 'saving' | 'synced'>('saved');
  const [roundText,      setRoundText]     = useState('');
  const [roundType,      setRoundType]     = useState('propose');
  const [connected,      setConnected]     = useState(false);
  const [livePresence,   setLivePresence]  = useState<string[]>(session.participants);
  const [insights,       setInsights]      = useState<SessionInsights | null>(null);
  const [delegateFrom,   setDelegateFrom]  = useState('');
  const [delegateTo,     setDelegateTo]    = useState('');
  const [delegateTask,   setDelegateTask]  = useState('');
  const [actionsDone,    setActionsDone]   = useState<Set<number>>(new Set());

  const { data: operations = [] } = useQuery({
    queryKey: ['collab-ops', session.session_id],
    queryFn: () => apiFetch<CollabOperation[]>(apiKey, `/collab/sessions/${session.session_id}/operations`),
    enabled: !!apiKey && !!session.session_id,
  });

  const { data: consensus, refetch: refetchConsensus } = useQuery({
    queryKey: ['collab-consensus', session.session_id],
    queryFn: () => apiFetch<ConsensusResult>(apiKey, `/collab/sessions/${session.session_id}/consensus`),
    enabled: !!apiKey && !!session.session_id,
    refetchInterval: 5_000,
  });

  useEffect(() => { setDraft(session.content ?? ''); }, [session.content]);
  useEffect(() => {
    if (typeof messagesEndRef.current?.scrollIntoView === 'function') {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  const handleMessage = useCallback((data: unknown) => {
    const msg = data as SessionMessage;
    if (msg.type === 'presence_join') {
      if (typeof msg.participant === 'string')
        setLivePresence((p) => Array.from(new Set([...p, msg.participant as string])));
      return;
    }
    if (msg.type === 'presence_leave') {
      if (typeof msg.participant === 'string')
        setLivePresence((p) => p.filter((x) => x !== msg.participant));
      return;
    }
    if (msg.operation) {
      const op      = msg.operation;
      const payload = op.operation;
      if (payload.type === 'content_update' && typeof payload.content === 'string') {
        setDraft(payload.content);
        setDraftStatus('synced');
      }
      if (msg.type === 'ack') {
        qc.invalidateQueries({ queryKey: ['collab-ops', session.session_id] });
        qc.invalidateQueries({ queryKey: ['collab-consensus', session.session_id] });
        return;
      }
      setMessages((prev) => [...prev, { type: msg.type, sender: op.author, content: operationContent(op) }]);
      qc.invalidateQueries({ queryKey: ['collab-ops', session.session_id] });
      qc.invalidateQueries({ queryKey: ['collab-consensus', session.session_id] });
      return;
    }
    setMessages((prev) => [...prev, msg]);
  }, [qc, session.session_id]);

  const { sendMessage } = useCollabSocket({
    sessionId: session.session_id,
    apiKey,
    onMessage: handleMessage,
    onOpen:  () => setConnected(true),
    onClose: () => setConnected(false),
  });

  const contentMutation = useMutation({
    mutationFn: (content: string) =>
      apiFetch<CollabOperation>(apiKey, `/collab/sessions/${session.session_id}/operations`, {
        method: 'POST',
        body: JSON.stringify({ type: 'content_update', content, author: 'human' }),
      }),
    onSuccess: () => {
      setDraftStatus('saved');
      qc.invalidateQueries({ queryKey: ['collab-ops',      session.session_id] });
      qc.invalidateQueries({ queryKey: ['collab-sessions'] });
    },
  });

  const roundMutation = useMutation({
    mutationFn: (body: { round_type: string; content: string }) =>
      apiFetch(apiKey, `/collab/sessions/${session.session_id}/rounds`, {
        method: 'POST',
        body: JSON.stringify({ agent_id: 'human', ...body }),
      }),
    onSuccess: () => {
      setRoundText('');
      qc.invalidateQueries({ queryKey: ['collab-ops',        session.session_id] });
      qc.invalidateQueries({ queryKey: ['collab-consensus',  session.session_id] });
    },
  });

  const insightsMutation = useMutation({
    mutationFn: () =>
      apiFetch<SessionInsights>(apiKey, `/collab/sessions/${session.session_id}/insights`, {
        method: 'POST',
        body: JSON.stringify({
          key_decisions: [], action_items: [], open_questions: [],
          agreement_level: 0, sentiment: 'neutral', summary: '', llm_powered: true,
        }),
      }),
    onSuccess: (data) => setInsights(data),
  });

  const delegateMutation = useMutation({
    mutationFn: () =>
      apiFetch(apiKey, `/collab/sessions/${session.session_id}/delegate`, {
        method: 'POST',
        body: JSON.stringify({ from_agent_id: delegateFrom, to_agent_id: delegateTo, sub_task: delegateTask }),
      }),
    onSuccess: () => { setDelegateFrom(''); setDelegateTo(''); setDelegateTask(''); },
  });

  const send = () => {
    if (!input.trim()) return;
    const payload = { type: 'message', content: input.trim(), author: 'human' };
    sendMessage(payload);
    setMessages((prev) => [...prev, { ...payload, sender: 'you' }]);
    setInput('');
  };

  const saveDraft = () => {
    setDraftStatus('saving');
    if (connected) { sendMessage({ type: 'content_update', content: draft, author: 'human' }); setDraftStatus('synced'); return; }
    contentMutation.mutate(draft);
  };

  const facilitateAI = () => {
    sendMessage({ type: 'facilitate', request: 'summarize_and_suggest', author: 'human' });
    setMessages((prev) => [...prev, { type: 'system', sender: 'AI Facilitator', content: 'Analyzing discussion state and generating next steps…' }]);
  };

  const showRounds = session.mode === 'debate' || session.mode === 'brainstorm';
  const showDraft  = session.mode === 'review'  || session.mode === 'suggest';
  const agreeVotes = operations.filter((o) => o.operation?.round_type === 'agree').length;
  const disaVotes  = operations.filter((o) => o.operation?.round_type === 'disagree').length;

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-4" data-testid="live-session">

      {/* ─── Left: Collaboration Arena ──────────────────────────────────── */}
      <div className="bg-card border border-border rounded-xl overflow-hidden flex flex-col min-h-[44rem]">

        {/* Mode-aware header */}
        <div className="px-4 py-3 border-b border-border flex items-center justify-between bg-muted/30">
          <div className="flex items-center gap-3">
            <div className={`p-1.5 rounded-lg ${MODE_CONFIG[session.mode]?.bg ?? 'bg-muted'}`}>
              {getModeIcon(session.mode, 'h-4 w-4')}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm">{session.name}</span>
                <ModeBadge mode={session.mode} />
              </div>
              <p className="text-[11px] text-muted-foreground font-mono">{session.session_id.slice(0, 18)}…</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={`flex items-center gap-1 text-xs font-medium ${connected ? 'text-green-500' : 'text-muted-foreground'}`}>
              {connected ? <><Wifi className="h-3 w-3" /> Live</> : <><WifiOff className="h-3 w-3" /> Offline</>}
            </span>
            <button
              onClick={facilitateAI}
              disabled={!connected}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs bg-purple-100 text-purple-700 hover:bg-purple-200 disabled:opacity-40 font-medium"
            >
              <Sparkles className="h-3 w-3" /> AI Facilitate
            </button>
            <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded hover:bg-accent">
              Close
            </button>
          </div>
        </div>

        {/* Participant presence bar */}
        <div className="px-4 py-2 border-b border-border flex items-center gap-3 flex-wrap bg-muted/10" data-testid="presence-bar">
          <span className="text-xs text-muted-foreground font-medium">In session:</span>
          {livePresence.length === 0
            ? <span className="text-xs text-muted-foreground italic">No participants listed</span>
            : livePresence.map((p) => <ParticipantPip key={p} id={p} live={connected} />)
          }
        </div>

        <div className="flex-1 min-h-0 flex flex-col">

          {/* Structured rounds (debate / brainstorm) */}
          {showRounds && (
            <section className="border-b border-border" data-testid="rounds-section">
              <div className="px-4 pt-3 pb-1 flex items-center justify-between">
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Rounds</span>
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1 text-green-600"><ThumbsUp className="h-3 w-3" /> {agreeVotes}</span>
                  <span className="flex items-center gap-1 text-red-500"><ThumbsDown className="h-3 w-3" /> {disaVotes}</span>
                  {(agreeVotes + disaVotes) > 0 && (
                    <div className="w-20 h-1.5 rounded-full bg-red-100 overflow-hidden">
                      <div className="h-full rounded-full bg-green-400 transition-all" style={{ width: `${(agreeVotes / (agreeVotes + disaVotes)) * 100}%` }} />
                    </div>
                  )}
                </div>
              </div>

              <div className="px-4 pb-2 space-y-1.5 max-h-36 overflow-y-auto">
                {operations.filter((o) => o.operation?.round_type).length === 0
                  ? <p className="text-xs text-muted-foreground text-center py-2">No rounds yet — submit below</p>
                  : operations.filter((o) => o.operation?.round_type).map((op) => {
                      const rt    = op.operation.round_type as string;
                      const rtCfg = ROUND_TYPES.find((r) => r.value === rt) ?? { label: rt, color: 'bg-muted text-muted-foreground' };
                      return (
                        <div key={op.operation_id} className="flex items-start gap-2">
                          <span className={`text-[11px] px-1.5 py-0.5 rounded font-medium shrink-0 ${rtCfg.color}`}>{rtCfg.label}</span>
                          <div className="flex-1 min-w-0">
                            <p className="text-xs text-foreground line-clamp-1">{operationContent(op)}</p>
                            <p className="text-[10px] text-muted-foreground">{op.author} · v{op.version}</p>
                          </div>
                        </div>
                      );
                    })
                }
              </div>

              <div className="px-4 pb-3 space-y-2">
                <div className="flex flex-wrap gap-1.5">
                  {ROUND_TYPES.map((rt) => (
                    <button
                      key={rt.value}
                      onClick={() => setRoundType(rt.value)}
                      className={`px-2.5 py-0.5 rounded-full text-xs font-medium transition-all ${roundType === rt.value ? `${rt.color} ring-2 ring-offset-1 ring-current` : 'bg-muted text-muted-foreground hover:bg-accent'}`}
                    >
                      {rt.label}
                    </button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input
                    value={roundText}
                    onChange={(e) => setRoundText(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && roundText.trim() && roundMutation.mutate({ round_type: roundType, content: roundText })}
                    placeholder={`${ROUND_TYPES.find((r) => r.value === roundType)?.label ?? 'Submit'} something…`}
                    className="flex-1 border border-input rounded-lg px-3 py-1.5 text-xs bg-background outline-none focus:ring-2 focus:ring-primary"
                  />
                  <button
                    onClick={() => roundMutation.mutate({ round_type: roundType, content: roundText })}
                    disabled={!roundText.trim() || roundMutation.isPending}
                    className="bg-primary text-primary-foreground px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
                  >
                    Submit
                  </button>
                </div>
              </div>
            </section>
          )}

          {/* Shared draft editor (review / suggest) */}
          {showDraft && (
            <section className="border-b border-border flex flex-col" data-testid="draft-editor">
              <div className="px-4 py-2 flex items-center justify-between">
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Shared draft</span>
                <div className="flex items-center gap-2 text-xs">
                  <span className="font-mono text-muted-foreground">v{operations.length}</span>
                  {draftStatus === 'saving' && <span className="text-amber-500 flex items-center gap-1"><Clock className="h-3 w-3" /> Saving…</span>}
                  {draftStatus === 'saved'  && <span className="text-muted-foreground flex items-center gap-1"><CheckCircle className="h-3 w-3" /> Saved</span>}
                  {draftStatus === 'synced' && <span className="text-green-500 flex items-center gap-1"><Wifi className="h-3 w-3" /> WS synced</span>}
                </div>
              </div>
              <textarea
                value={draft}
                onChange={(e) => { setDraft(e.target.value); setDraftStatus('saving'); }}
                className="flex-1 min-h-[9rem] max-h-52 p-4 text-sm bg-background outline-none resize-none font-mono leading-relaxed"
                placeholder="Draft a code review note, incident update, or spec — changes sync in real time…"
              />
              <div className="border-t border-border p-2 flex justify-end">
                <button
                  onClick={saveDraft}
                  disabled={contentMutation.isPending}
                  className="bg-primary text-primary-foreground px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-50"
                >
                  {connected ? 'Sync via WS' : 'Save draft'}
                </button>
              </div>
            </section>
          )}

          {/* Live messages */}
          <section className="flex-1 flex flex-col min-h-0" data-testid="messages-panel">
            <div className="px-4 py-2 border-b border-border flex items-center gap-2">
              <MessageSquare className="h-3.5 w-3.5 text-muted-foreground" />
              <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Live Messages</span>
            </div>
            <div className="flex-1 overflow-y-auto p-4 space-y-2">
              {messages.length === 0 && (
                <p className="text-sm text-muted-foreground text-center py-6">No messages yet — start collaborating</p>
              )}
              {messages.map((msg, i) => {
                const isYou    = msg.sender === 'you';
                const isSystem = msg.type === 'system';
                if (isSystem) {
                  return (
                    <div key={i} className="flex justify-center">
                      <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs bg-purple-50 text-purple-700 border border-purple-200">
                        <Sparkles className="h-3 w-3" /> {msg.content}
                      </div>
                    </div>
                  );
                }
                return (
                  <div key={i} className={`flex items-end gap-2 ${isYou ? 'justify-end' : 'justify-start'}`}>
                    {!isYou && (
                      <div className="h-6 w-6 rounded-full bg-muted flex items-center justify-center text-[10px] font-bold shrink-0">
                        {(msg.sender ?? '?')[0].toUpperCase()}
                      </div>
                    )}
                    <div className="max-w-xs space-y-0.5">
                      {!isYou && <p className="text-[10px] text-muted-foreground pl-1">{msg.sender}</p>}
                      <div className={`rounded-2xl px-3 py-2 text-sm leading-snug ${isYou ? 'bg-primary text-primary-foreground rounded-br-sm' : 'bg-muted text-foreground rounded-bl-sm'}`}>
                        {msg.content ?? JSON.stringify(msg)}
                      </div>
                    </div>
                  </div>
                );
              })}
              <div ref={messagesEndRef} />
            </div>
            <div className="border-t border-border p-3 flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
                placeholder={connected ? 'Send a message…' : 'Connecting…'}
                disabled={!connected}
                className="flex-1 border border-input rounded-xl px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
              />
              <button
                onClick={send}
                disabled={!input.trim() || !connected}
                className="bg-primary text-primary-foreground p-2 rounded-xl hover:opacity-90 disabled:opacity-50"
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
          </section>
        </div>
      </div>

      {/* ─── Right: Intelligence Panel ───────────────────────────────────── */}
      <aside className="space-y-4 overflow-y-auto max-h-[44rem]">

        {/* Consensus status card */}
        <div className="bg-card border border-border rounded-xl p-4" data-testid="consensus-card">
          <h3 className="font-semibold text-sm flex items-center gap-2 mb-3">
            <CheckCircle className="h-4 w-4 text-green-500" /> Consensus Status
          </h3>
          <div className={`rounded-xl p-3 flex items-start gap-3 ${consensus?.agreed ? 'bg-green-50 border border-green-200' : 'bg-muted border border-border'}`}>
            {consensus?.agreed
              ? <CheckCircle className="h-6 w-6 text-green-500 shrink-0 mt-0.5" />
              : <AlertCircle className="h-6 w-6 text-muted-foreground shrink-0 mt-0.5" />
            }
            <div className="flex-1 min-w-0">
              <p className={`font-semibold text-sm ${consensus?.agreed ? 'text-green-700' : 'text-muted-foreground'}`}>
                {consensus?.agreed ? 'Consensus reached' : 'No consensus yet'}
              </p>
              {consensus?.summary && (
                <p className="text-xs text-muted-foreground mt-1 line-clamp-3">{consensus.summary}</p>
              )}
              {consensus?.dissenter && (
                <p className="text-xs text-orange-600 mt-1 flex items-center gap-1">
                  <XCircle className="h-3 w-3" /> Dissenter: {consensus.dissenter}
                </p>
              )}
            </div>
          </div>
          <button
            onClick={() => void refetchConsensus()}
            className="mt-2 w-full text-xs text-muted-foreground hover:text-foreground flex items-center justify-center gap-1 py-1 rounded hover:bg-accent transition-colors"
          >
            <Activity className="h-3 w-3" /> Refresh
          </button>
        </div>

        {/* Session insights panel */}
        <div className="bg-card border border-border rounded-xl p-4" data-testid="insights-panel">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold text-sm flex items-center gap-2">
              <Brain className="h-4 w-4 text-purple-500" /> Session Insights
            </h3>
            <button
              data-testid="generate-insights-btn"
              onClick={() => insightsMutation.mutate()}
              disabled={insightsMutation.isPending}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs bg-purple-100 text-purple-700 hover:bg-purple-200 disabled:opacity-50 font-medium"
            >
              <Sparkles className="h-3 w-3" />
              {insightsMutation.isPending ? 'Generating…' : 'Generate Insights'}
            </button>
          </div>

          {!insights ? (
            <p className="text-xs text-muted-foreground text-center py-4">
              Click "Generate Insights" to analyze this session with AI
            </p>
          ) : (
            <div className="space-y-3">
              {insights.summary && (
                <p className="text-xs text-muted-foreground italic border-l-2 border-purple-300 pl-2">{insights.summary}</p>
              )}
              <div className="flex gap-2 flex-wrap">
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${insights.agreement_level >= 0.7 ? 'bg-green-100 text-green-700' : insights.agreement_level >= 0.4 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-700'}`}>
                  {Math.round((insights.agreement_level ?? 0) * 100)}% agreement
                </span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground font-medium capitalize">
                  {insights.sentiment}
                </span>
              </div>
              {insights.key_decisions.length > 0 && (
                <div>
                  <p className="text-xs font-semibold mb-1.5 flex items-center gap-1"><Target className="h-3 w-3 text-blue-500" /> Key Decisions</p>
                  <ul className="space-y-1">
                    {insights.key_decisions.map((d, i) => (
                      <li key={i} className="text-xs text-muted-foreground flex items-start gap-1.5">
                        <ArrowRight className="h-3 w-3 mt-0.5 shrink-0 text-blue-400" /> {d}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {insights.action_items.length > 0 && (
                <div>
                  <p className="text-xs font-semibold mb-1.5 flex items-center gap-1"><Zap className="h-3 w-3 text-amber-500" /> Action Items</p>
                  <ul className="space-y-1.5">
                    {insights.action_items.map((item, i) => (
                      <li key={i} className="flex items-start gap-2">
                        <input
                          type="checkbox"
                          checked={actionsDone.has(i)}
                          onChange={() => setActionsDone((prev) => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n; })}
                          className="h-3 w-3 mt-0.5 accent-primary"
                        />
                        <span className={`text-xs leading-tight ${actionsDone.has(i) ? 'line-through text-muted-foreground' : ''}`}>{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {insights.open_questions.length > 0 && (
                <div>
                  <p className="text-xs font-semibold mb-1.5 flex items-center gap-1"><Lightbulb className="h-3 w-3 text-yellow-500" /> Open Questions</p>
                  <ul className="space-y-1">
                    {insights.open_questions.map((q, i) => (
                      <li key={i} className="text-xs text-muted-foreground flex items-start gap-1.5">
                        <AlertCircle className="h-3 w-3 mt-0.5 shrink-0 text-yellow-400" /> {q}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Delegation panel */}
        <div className="bg-card border border-border rounded-xl p-4" data-testid="delegate-panel">
          <h3 className="font-semibold text-sm flex items-center gap-2 mb-3">
            <Bot className="h-4 w-4 text-sky-500" /> Delegate Task
          </h3>
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <input
                value={delegateFrom}
                onChange={(e) => setDelegateFrom(e.target.value)}
                placeholder="From agent ID"
                className="flex-1 border border-input rounded-lg px-3 py-1.5 text-xs bg-background outline-none focus:ring-2 focus:ring-primary"
              />
              <ArrowRight className="h-4 w-4 text-muted-foreground shrink-0" />
              <input
                value={delegateTo}
                onChange={(e) => setDelegateTo(e.target.value)}
                placeholder="To agent ID"
                className="flex-1 border border-input rounded-lg px-3 py-1.5 text-xs bg-background outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
            <input
              value={delegateTask}
              onChange={(e) => setDelegateTask(e.target.value)}
              placeholder="Sub-task description"
              className="w-full border border-input rounded-lg px-3 py-1.5 text-xs bg-background outline-none focus:ring-2 focus:ring-primary"
            />
            <button
              onClick={() => delegateMutation.mutate()}
              disabled={!delegateFrom.trim() || !delegateTo.trim() || !delegateTask.trim() || delegateMutation.isPending}
              className="w-full bg-sky-500 text-white py-1.5 rounded-lg text-xs font-semibold hover:bg-sky-600 disabled:opacity-50 flex items-center justify-center gap-1.5 transition-colors"
            >
              <Bot className="h-3.5 w-3.5" />
              {delegateMutation.isPending ? 'Delegating…' : 'Delegate Task'}
            </button>
            {delegateMutation.isSuccess && (
              <p className="text-xs text-green-600 text-center flex items-center justify-center gap-1">
                <CheckCircle className="h-3 w-3" /> Delegated successfully
              </p>
            )}
          </div>
        </div>

        {/* Operations log */}
        <div className="bg-card border border-border rounded-xl p-4">
          <h3 className="font-semibold text-sm flex items-center gap-2 mb-3">
            <Clock className="h-4 w-4 text-muted-foreground" /> Operations Log
          </h3>
          <div className="space-y-2 max-h-52 overflow-y-auto">
            {operations.length === 0
              ? <p className="text-xs text-muted-foreground text-center py-2">No operations yet</p>
              : [...operations].reverse().map((op) => (
                  <div key={op.operation_id} className="flex items-start gap-2 text-xs">
                    <span className="font-mono text-[10px] bg-muted px-1.5 py-0.5 rounded text-muted-foreground shrink-0 mt-0.5">
                      v{op.version}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="truncate text-foreground">{operationContent(op)}</p>
                      <p className="text-[10px] text-muted-foreground">{op.author} · {timeAgo(op.created_at)}</p>
                    </div>
                  </div>
                ))
            }
          </div>
        </div>
      </aside>
    </div>
  );
}

// ─── CollaborationPage ────────────────────────────────────────────────────────

export function CollaborationPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const qc     = useQueryClient();
  const [activeSession, setActiveSession] = useState<CollabSession | null>(null);
  const [showCreate,    setShowCreate]    = useState(false);
  const [selectedMode,  setSelectedMode]  = useState('review');
  const [statusFilter,  setStatusFilter]  = useState<'all' | 'active' | 'closed'>('all');
  const [form, setForm] = useState({ name: '', goal_id: '', agent_id: '', participants: '' });

  const { data: sessions = [], isLoading, error } = useQuery({
    queryKey: ['collab-sessions'],
    queryFn: () => apiFetch<CollabSession[]>(apiKey, '/collab/sessions'),
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<CollabSession>(apiKey, '/collab/sessions', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name.trim() || 'Collaboration Session',
          mode: selectedMode,
          participants: form.participants.split(',').map((p) => p.trim()).filter(Boolean),
          goal_id:  form.goal_id.trim()  || null,
          agent_id: form.agent_id.trim() || null,
        }),
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['collab-sessions'] });
      setShowCreate(false);
      setForm({ name: '', goal_id: '', agent_id: '', participants: '' });
      setActiveSession(data);
    },
  });

  const applyTemplate = (tpl: typeof TEMPLATES[number]) => {
    setForm((f) => ({ ...f, name: tpl.name, participants: tpl.participants }));
    setSelectedMode(tpl.mode);
  };

  const filtered = sessions.filter((s) => statusFilter === 'all' ? true : s.status === statusFilter);

  return (
    <div className="space-y-6">

      {/* Page header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Collaboration</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Real-time AI-powered sessions — debate, review, brainstorm, and build consensus
          </p>
        </div>
        <button
          data-testid="create-session-btn"
          onClick={() => setShowCreate((v) => !v)}
          className="flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90 font-medium"
        >
          {showCreate ? '✕ Cancel' : '+ New Session'}
        </button>
      </div>

      {/* Create session panel */}
      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-5 space-y-4">
          <h3 className="font-semibold text-sm">New Collaboration Session</h3>

          {/* Quick templates */}
          <div>
            <p className="text-xs text-muted-foreground mb-2 font-medium uppercase tracking-wide">Quick templates</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {TEMPLATES.map((tpl) => (
                <button
                  key={tpl.name}
                  onClick={() => applyTemplate(tpl)}
                  className="text-left p-3 rounded-lg border border-border hover:border-primary hover:bg-accent transition-all"
                >
                  <ModeBadge mode={tpl.mode} />
                  <p className="font-medium text-xs mt-1.5">{tpl.name}</p>
                  <p className="text-muted-foreground text-[11px] mt-0.5">{tpl.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Mode selector */}
          <div>
            <p className="text-xs text-muted-foreground mb-2 font-medium uppercase tracking-wide">Session mode</p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {Object.entries(MODE_CONFIG).map(([mode, cfg]) => (
                <button
                  key={mode}
                  onClick={() => setSelectedMode(mode)}
                  className={`p-3 rounded-lg border text-left transition-all ${selectedMode === mode ? 'border-primary bg-primary/5 ring-1 ring-primary/20' : 'border-border hover:bg-accent'}`}
                >
                  <div className={`flex items-center gap-1.5 text-xs font-semibold ${cfg.color}`}>
                    {getModeIcon(mode)} {cfg.label}
                  </div>
                  <p className="text-muted-foreground text-[11px] mt-1">{cfg.desc}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Form fields */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Session name"
              className="border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            />
            <input
              value={form.goal_id}
              onChange={(e) => setForm((f) => ({ ...f, goal_id: e.target.value }))}
              placeholder="Goal ID"
              className="border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            />
            <input
              value={form.agent_id}
              onChange={(e) => setForm((f) => ({ ...f, agent_id: e.target.value }))}
              placeholder="Agent ID"
              className="border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            />
            <input
              value={form.participants}
              onChange={(e) => setForm((f) => ({ ...f, participants: e.target.value }))}
              placeholder="Participants, comma separated"
              className="border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            />
          </div>

          {createMutation.isError && (
            <p className="text-xs text-red-600 flex items-center gap-1">
              <AlertCircle className="h-3 w-3" /> {String(createMutation.error)}
            </p>
          )}
          <div className="flex justify-end">
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
              className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating…' : 'Create Session'}
            </button>
          </div>
        </div>
      )}

      {/* Active live session */}
      {activeSession && (
        <LiveSessionPanel session={activeSession} apiKey={apiKey} onClose={() => setActiveSession(null)} />
      )}

      {/* Status filter bar + sessions grid */}
      <div className="space-y-4">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-muted-foreground font-medium">Status:</span>
          {(['all', 'active', 'closed'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${statusFilter === s ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-accent'}`}
            >
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </button>
          ))}
          <span className="ml-auto text-xs text-muted-foreground">
            {filtered.length} session{filtered.length !== 1 ? 's' : ''}
          </span>
        </div>

        {isLoading ? (
          <div className="py-16 text-center text-sm text-muted-foreground">
            <Activity className="h-8 w-8 mx-auto mb-2 opacity-30 animate-pulse" />
            Loading sessions…
          </div>
        ) : error ? (
          <div className="py-16 text-center text-sm text-red-500">
            <AlertCircle className="h-8 w-8 mx-auto mb-2" />
            Failed to load sessions.
          </div>
        ) : filtered.length === 0 ? (
          <div className="py-16 text-center text-sm text-muted-foreground border border-dashed border-border rounded-xl">
            <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-30" />
            <p className="font-medium">No sessions yet</p>
            <p className="mt-1 text-xs">Create a session to start collaborating in real time</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4" data-testid="sessions-list">
            {filtered.map((s) => (
              <div
                key={s.session_id}
                data-testid="session-card"
                className="bg-card border border-border rounded-xl p-4 hover:border-primary/50 hover:shadow-sm transition-all cursor-pointer group"
                onClick={() => setActiveSession(s)}
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="flex-1 min-w-0 mr-2">
                    <p className="font-semibold text-sm truncate">{s.name}</p>
                    <p className="text-[11px] text-muted-foreground font-mono mt-0.5 truncate">{s.session_id}</p>
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    <ModeBadge mode={s.mode} />
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium flex items-center gap-1 ${s.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-muted text-muted-foreground'}`}>
                      {s.status === 'active' && <span className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />}
                      {s.status}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
                  <span className="flex items-center gap-1"><Users className="h-3 w-3" /> {s.participant_count}</span>
                  {s.goal_id  && <span className="flex items-center gap-1 font-mono truncate max-w-[6rem]"><Target className="h-3 w-3 shrink-0" />{s.goal_id}</span>}
                  {s.agent_id && <span className="flex items-center gap-1 font-mono truncate max-w-[6rem]"><Bot    className="h-3 w-3 shrink-0" />{s.agent_id}</span>}
                </div>

                {s.participants.length > 0 && (
                  <div className="flex items-center gap-1 mt-3">
                    {s.participants.slice(0, 4).map((p) => {
                      const isAgent  = p.startsWith('agent');
                      const initials = p.split(':')[1]?.slice(0, 2).toUpperCase() ?? p.slice(0, 2).toUpperCase();
                      return (
                        <div key={p} title={p}
                          className={`h-6 w-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white ${isAgent ? 'bg-violet-400' : 'bg-sky-400'}`}>
                          {initials}
                        </div>
                      );
                    })}
                    {s.participants.length > 4 && (
                      <div className="h-6 w-6 rounded-full bg-muted flex items-center justify-center text-[10px] text-muted-foreground font-medium">
                        +{s.participants.length - 4}
                      </div>
                    )}
                  </div>
                )}

                <div className="flex items-center justify-between mt-3 pt-3 border-t border-border">
                  <span className="text-[11px] text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" /> {timeAgo(s.created_at)}
                  </span>
                  <span className="text-xs text-primary font-medium opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1">
                    Join <ArrowRight className="h-3 w-3" />
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
