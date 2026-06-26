import { useCallback, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle, MessageSquare, Send, Users, Wifi, WifiOff } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { useCollabSocket } from '@/lib/ws/useCollabSocket';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

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

interface SessionMessage {
  type: string;
  sender?: string;
  content?: string;
  operation?: CollabOperation;
  [key: string]: unknown;
}

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

function operationContent(operation: CollabOperation): string {
  const payload = operation.operation;
  if (typeof payload.content === 'string') return payload.content;
  if (typeof payload.text === 'string') return payload.text;
  return JSON.stringify(payload);
}

function LiveSessionPanel({
  session,
  apiKey,
  onClose,
}: {
  session: CollabSession;
  apiKey: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [messages, setMessages] = useState<SessionMessage[]>([]);
  const [input, setInput] = useState('');
  const [draft, setDraft] = useState(session.content ?? '');
  const [roundText, setRoundText] = useState('');
  const [connected, setConnected] = useState(false);
  const [participants, setParticipants] = useState(1);

  const { data: operations = [] } = useQuery({
    queryKey: ['collab-ops', session.session_id],
    queryFn: () => apiFetch<CollabOperation[]>(apiKey, `/collab/sessions/${session.session_id}/operations`),
    enabled: !!apiKey && !!session.session_id,
  });

  const { data: consensus } = useQuery({
    queryKey: ['collab-consensus', session.session_id],
    queryFn: () => apiFetch<ConsensusResult>(apiKey, `/collab/sessions/${session.session_id}/consensus`),
    enabled: !!apiKey && !!session.session_id,
    refetchInterval: 5_000,
  });

  useEffect(() => setDraft(session.content ?? ''), [session.content]);

  const handleMessage = useCallback((data: unknown) => {
    const msg = data as SessionMessage;
    if (msg.type === 'presence_join') { setParticipants(msg.participants as number); return; }
    if (msg.type === 'presence_leave') { setParticipants(msg.participants as number); return; }
    if (msg.operation) {
      const op = msg.operation;
      const payload = op.operation;
      if (payload.type === 'content_update' && typeof payload.content === 'string') {
        setDraft(payload.content);
      }
      if (msg.type === 'ack') {
        qc.invalidateQueries({ queryKey: ['collab-ops', session.session_id] });
        qc.invalidateQueries({ queryKey: ['collab-consensus', session.session_id] });
        return;
      }
      setMessages((prev) => [
        ...prev,
        {
          type: msg.type,
          sender: op.author,
          content: operationContent(op),
        },
      ]);
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
    onOpen: () => setConnected(true),
    onClose: () => setConnected(false),
  });

  const contentMutation = useMutation({
    mutationFn: (content: string) =>
      apiFetch<CollabOperation>(apiKey, `/collab/sessions/${session.session_id}/operations`, {
        method: 'POST',
        body: JSON.stringify({ type: 'content_update', content, author: 'human' }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['collab-ops', session.session_id] });
      qc.invalidateQueries({ queryKey: ['collab-sessions'] });
    },
  });

  const roundMutation = useMutation({
    mutationFn: (body: { round_type: string; content: string }) =>
      apiFetch<CollabOperation>(apiKey, `/collab/sessions/${session.session_id}/rounds`, {
        method: 'POST',
        body: JSON.stringify({ agent_id: 'human', ...body }),
      }),
    onSuccess: () => {
      setRoundText('');
      qc.invalidateQueries({ queryKey: ['collab-ops', session.session_id] });
      qc.invalidateQueries({ queryKey: ['collab-consensus', session.session_id] });
    },
  });

  const send = () => {
    if (!input.trim()) return;
    const payload = { type: 'message', content: input.trim(), author: 'human' };
    sendMessage(payload);
    setMessages((prev) => [...prev, { ...payload, sender: 'you' }]);
    setInput('');
  };

  const saveDraft = () => {
    if (connected) {
      sendMessage({ type: 'content_update', content: draft, author: 'human' });
      return;
    }
    contentMutation.mutate(draft);
  };

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[1fr_360px] gap-4">
      <div className="bg-card border border-border rounded-xl overflow-hidden flex flex-col min-h-[32rem]">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span className="font-medium text-sm">{session.name}</span>
              <span className="text-xs text-muted-foreground">{session.mode}</span>
            </div>
            <p className="text-xs text-muted-foreground font-mono">{session.session_id}</p>
          </div>
          <div className="flex items-center gap-3">
            <span className={`flex items-center gap-1 text-xs ${connected ? 'text-green-500' : 'text-muted-foreground'}`}>
              {connected ? <><Wifi className="h-3 w-3" /> Live</> : <><WifiOff className="h-3 w-3" /> Disconnected</>}
            </span>
            <span className="text-xs text-muted-foreground">
              {participants} participant{participants !== 1 ? 's' : ''}
            </span>
            <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-sm">
              Close
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 flex-1 min-h-0">
          <section className="border-r border-border flex flex-col min-h-0">
            <div className="px-4 py-2 border-b border-border font-medium text-sm">Live messages</div>
            <div className="flex-1 overflow-y-auto p-4 space-y-2">
              {messages.length === 0 && operations.length === 0 && (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No messages yet. Send one to start collaborating.
                </p>
              )}
              {operations.map((op) => (
                <div key={op.operation_id} className="rounded-xl px-3 py-2 text-sm bg-muted text-foreground">
                  <p className="text-xs opacity-60 mb-0.5">{op.author} · v{op.version}</p>
                  {operationContent(op)}
                </div>
              ))}
              {messages.map((msg, i) => (
                <div key={i} className={`flex ${msg.sender === 'you' ? 'justify-end' : 'justify-start'}`}>
                  <div className={`max-w-sm rounded-xl px-3 py-2 text-sm ${msg.sender === 'you' ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground'}`}>
                    {msg.content ?? JSON.stringify(msg)}
                  </div>
                </div>
              ))}
            </div>
            <div className="border-t border-border p-3 flex gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
                placeholder="Send a message..."
                disabled={!connected}
                className="flex-1 border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary disabled:opacity-50"
              />
              <button onClick={send} disabled={!input.trim() || !connected} className="bg-primary text-primary-foreground p-2 rounded-lg hover:opacity-90 disabled:opacity-50">
                <Send className="h-4 w-4" />
              </button>
            </div>
          </section>

          <section className="flex flex-col min-h-0">
            <div className="px-4 py-2 border-b border-border font-medium text-sm">Shared draft</div>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="flex-1 min-h-64 p-4 text-sm bg-background outline-none resize-none"
              placeholder="Draft a Jira triage note, Confluence summary, or incident update..."
            />
            <div className="border-t border-border p-3 flex justify-end">
              <button
                onClick={saveDraft}
                disabled={contentMutation.isPending}
                className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
              >
                Save draft
              </button>
            </div>
          </section>
        </div>
      </div>

      <aside className="bg-card border border-border rounded-xl p-4 space-y-4">
        <div>
          <h3 className="font-semibold text-sm flex items-center gap-2"><Users className="h-4 w-4" /> Participants</h3>
          <div className="mt-2 flex flex-wrap gap-1">
            {session.participants.length ? session.participants.map((p) => (
              <span key={p} className="text-xs rounded-full bg-muted px-2 py-1">{p}</span>
            )) : <span className="text-xs text-muted-foreground">No participants listed</span>}
          </div>
        </div>

        <div>
          <h3 className="font-semibold text-sm flex items-center gap-2"><CheckCircle className="h-4 w-4" /> Consensus</h3>
          <div className="mt-2 rounded-lg border border-border p-3 text-sm">
            <p className={consensus?.agreed ? 'text-green-600' : 'text-muted-foreground'}>
              {consensus?.agreed ? 'Consensus reached' : 'No consensus yet'}
            </p>
            {consensus?.summary && <p className="mt-1 text-xs text-muted-foreground">{consensus.summary}</p>}
          </div>
          <textarea
            value={roundText}
            onChange={(e) => setRoundText(e.target.value)}
            rows={3}
            placeholder="Add proposal, critique, or agreement..."
            className="mt-3 w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary resize-none"
          />
          <div className="mt-2 grid grid-cols-2 gap-2">
            <button onClick={() => roundMutation.mutate({ round_type: 'propose', content: roundText })} disabled={!roundText.trim()} className="border border-border rounded-lg px-3 py-1.5 text-xs hover:bg-accent disabled:opacity-50">Propose</button>
            <button onClick={() => roundMutation.mutate({ round_type: 'agree', content: roundText || 'Agreed' })} className="border border-border rounded-lg px-3 py-1.5 text-xs hover:bg-accent">Agree</button>
          </div>
        </div>
      </aside>
    </div>
  );
}

export function CollaborationPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const qc = useQueryClient();
  const [activeSession, setActiveSession] = useState<CollabSession | null>(null);
  const [showCreate, setShowCreate] = useState(false);
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
          mode: 'review',
          participants: form.participants.split(',').map((p) => p.trim()).filter(Boolean),
          goal_id: form.goal_id.trim() || null,
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

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Collaboration</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Real-time collaborative sessions with agents and team members
        </p>
      </div>

      <div className="flex justify-end">
        <button onClick={() => setShowCreate((v) => !v)} className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90">
          {showCreate ? 'Cancel' : '+ New Session'}
        </button>
      </div>

      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-5 space-y-3">
          <h3 className="font-medium text-sm">New Collaboration Session</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Session name" className="border border-input rounded-lg px-3 py-2 text-sm bg-background" />
            <input value={form.goal_id} onChange={(e) => setForm((f) => ({ ...f, goal_id: e.target.value }))} placeholder="Goal ID" className="border border-input rounded-lg px-3 py-2 text-sm bg-background" />
            <input value={form.agent_id} onChange={(e) => setForm((f) => ({ ...f, agent_id: e.target.value }))} placeholder="Agent ID" className="border border-input rounded-lg px-3 py-2 text-sm bg-background" />
            <input value={form.participants} onChange={(e) => setForm((f) => ({ ...f, participants: e.target.value }))} placeholder="Participants, comma separated" className="border border-input rounded-lg px-3 py-2 text-sm bg-background" />
          </div>
          {createMutation.isError && <p className="text-xs text-red-600">{String(createMutation.error)}</p>}
          <div className="flex justify-end">
            <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending} className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50">
              {createMutation.isPending ? 'Creating...' : 'Create Session'}
            </button>
          </div>
        </div>
      )}

      {activeSession && <LiveSessionPanel session={activeSession} apiKey={apiKey} onClose={() => setActiveSession(null)} />}

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="py-10 text-center text-sm text-muted-foreground">Loading sessions...</div>
        ) : error ? (
          <div className="py-10 text-center text-sm text-red-500">Failed to load sessions.</div>
        ) : sessions.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted-foreground">
            <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-30" />
            <p className="font-medium">No sessions yet</p>
            <p className="mt-1">Create a session to collaborate in real time</p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                {['Session', 'Goal', 'Agent', 'Participants', 'Created', 'Actions'].map((h) => (
                  <th key={h} className="text-left px-4 py-3 font-medium text-muted-foreground">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sessions.map((s) => (
                <tr key={s.session_id} className="hover:bg-accent/50 transition-colors">
                  <td className="px-4 py-3"><p className="font-medium">{s.name}</p><p className="text-xs text-muted-foreground font-mono mt-0.5">{s.session_id}</p></td>
                  <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{s.goal_id ?? '-'}</td>
                  <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{s.agent_id ?? '-'}</td>
                  <td className="px-4 py-3 text-muted-foreground">{s.participant_count}</td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">{s.created_at ? new Date(s.created_at).toLocaleString() : '-'}</td>
                  <td className="px-4 py-3"><button onClick={() => setActiveSession(s)} className="text-primary hover:opacity-70 text-sm">Join</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
