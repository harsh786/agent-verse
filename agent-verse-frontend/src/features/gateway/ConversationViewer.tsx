/**
 * ConversationViewer — multi-turn conversation inspector across channels.
 * JARVIS motion: JARVISPageShell + JARVISStagger turn items.
 *
 * Data: GET /v1/org/{orgId}/commands returns `{ org_id, commands, total }` —
 * newest-first Universal Command Gateway records (app/org/router.py
 * `org_list_commands`, rows from `OrgCommandStore`). There is no
 * `conversations` field, so conversations are derived here by grouping commands
 * on (channel, conversation_id). Command history holds only what the user sent
 * plus its status/goal/error; assistant replies are not recorded, and the UI
 * says so instead of inventing them.
 */
import { useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  MessageSquare, Search, User,
  MessageCircle, Hash, Terminal, Mail, Webhook, RefreshCw, AlertTriangle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { Input } from '@/components/ui/input';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

/** One row of `commands` from GET /v1/org/{id}/commands. */
export interface OrgCommand {
  command_id: string;
  command: string;
  channel: string;
  status: string;
  conversation_id?: string | null;
  goal_id?: string;
  error?: string;
  submitted_at: string;
}

interface OrgCommandsResponse {
  org_id: string;
  commands: OrgCommand[];
  total: number;
}

interface Conversation {
  key: string;
  channel: string;
  /** null when the command was sent without a conversation id (single command). */
  conversation_id: string | null;
  /** Oldest first. */
  commands: OrgCommand[];
  last: OrgCommand;
}

const COMMAND_LIMIT = 100; // backend max for this endpoint

const CHANNEL_ICON: Record<string, { icon: React.ElementType; color: string }> = {
  rest:     { icon: Terminal,      color: 'text-[#475569]'  },
  telegram: { icon: MessageCircle, color: 'text-blue-400'   },
  slack:    { icon: Hash,          color: 'text-purple-400' },
  email:    { icon: Mail,          color: 'text-yellow-400' },
  webhook:  { icon: Webhook,       color: 'text-orange-400' },
};

const STATUS_COLOR: Record<string, string> = {
  completed: 'text-emerald-400',
  failed:    'text-red-400',
  queued:    'text-[#94A3B8]',
  running:   'text-[#00D4FF]',
};

function groupConversations(commands: OrgCommand[]): Conversation[] {
  const byKey = new Map<string, Conversation>();
  for (const cmd of commands) {
    const cid = cmd.conversation_id || null;
    const key = cid ? `${cmd.channel}:${cid}` : `cmd:${cmd.command_id}`;
    const conv = byKey.get(key);
    if (conv) conv.commands.push(cmd);
    else byKey.set(key, { key, channel: cmd.channel, conversation_id: cid, commands: [cmd], last: cmd });
  }
  const ts = (c: OrgCommand) => Date.parse(c.submitted_at) || 0;
  const list = [...byKey.values()];
  for (const conv of list) {
    conv.commands.sort((a, b) => ts(a) - ts(b));
    conv.last = conv.commands[conv.commands.length - 1];
  }
  return list.sort((a, b) => ts(b.last) - ts(a.last));
}

interface ConversationViewerProps { orgId: string; }

export function ConversationViewer({ orgId }: ConversationViewerProps) {
  const [search, setSearch]         = useState('');
  const [selected, setSelected]     = useState<string | null>(null);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['org-commands', orgId, COMMAND_LIMIT],
    queryFn: () => apiFetch<OrgCommandsResponse>(`/v1/org/${orgId}/commands?limit=${COMMAND_LIMIT}`),
    staleTime: 30_000,
  });

  const conversations = useMemo(() => groupConversations(data?.commands ?? []), [data]);
  const q = search.trim().toLowerCase();
  const filtered = conversations.filter(c =>
    !q
      || (c.conversation_id?.toLowerCase().includes(q) ?? false)
      || c.commands.some(cmd => cmd.command.toLowerCase().includes(q)),
  );

  const selectedConv = conversations.find(c => c.key === selected);
  const label = (c: Conversation) => c.conversation_id ?? c.last.command;

  return (
    <JARVISPageShell className="flex h-full gap-4">
      {/* ── Left: conversation list ── */}
      <div className="flex flex-col w-72 shrink-0 h-full">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-[#F1F5F9] flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-[#00D4FF]" aria-hidden />
            Conversations
          </h2>
          <button onClick={() => refetch()} disabled={isFetching} aria-label="Refresh" style={{ touchAction: 'manipulation' }} className="p-1.5 rounded text-[#475569] hover:text-[#94A3B8] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50">
            <RefreshCw className={cn('h-3.5 w-3.5', isFetching && 'animate-spin')} aria-hidden />
          </button>
        </div>

        <div className="relative mb-3">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#475569]" aria-hidden />
          <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search…" className="pl-8 bg-[#0F1623] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569] text-xs h-8" aria-label="Search conversations" />
        </div>

        <div className="flex-1 overflow-y-auto space-y-1" role="list" aria-label="Conversations">
          {isLoading
            ? [1,2,3].map(i => <div key={i} className="h-16 rounded-lg bg-[#0F1623] animate-pulse" aria-hidden />)
            : isError
            ? (
              <p role="alert" className="text-xs text-red-400 text-center py-8 flex items-center justify-center gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
                Couldn&apos;t load conversations.
              </p>
            )
            : filtered.length === 0
            ? <p className="text-xs text-[#475569] text-center py-8">No conversations yet.</p>
            : filtered.map(conv => {
              const chanConf = CHANNEL_ICON[conv.channel] ?? CHANNEL_ICON.rest;
              const ChanIcon = chanConf.icon;
              return (
                <button
                  key={conv.key}
                  onClick={() => setSelected(conv.key)}
                  role="listitem"
                  style={{ touchAction: 'manipulation' }}
                  className={cn(
                    'w-full text-left flex items-start gap-2.5 px-3 py-2.5 rounded-xl transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
                    selected === conv.key
                      ? 'bg-[#00D4FF]/10 border border-[#00D4FF]/20'
                      : 'hover:bg-[#1A1F2E] border border-transparent',
                  )}
                >
                  <ChanIcon className={cn('h-3.5 w-3.5 flex-shrink-0 mt-0.5', chanConf.color)} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-[#94A3B8] truncate">{label(conv)}</span>
                      <span className="text-[10px] text-[#475569] flex-shrink-0 ml-1">
                        {conv.commands.length} {conv.commands.length === 1 ? 'cmd' : 'cmds'}
                      </span>
                    </div>
                    {conv.conversation_id && (
                      <p className="text-[11px] text-[#475569] truncate">{conv.last.command}</p>
                    )}
                  </div>
                </button>
              );
            })
          }
        </div>
      </div>

      {/* ── Right: commands in the selected conversation ── */}
      <div className="flex-1 h-full overflow-y-auto">
        {!selectedConv ? (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <MessageSquare className="h-12 w-12 text-[#1E2535]" aria-hidden />
            <p className="text-sm text-[#475569]">Select a conversation to inspect</p>
          </div>
        ) : (
          <AnimatePresence mode="wait">
            <motion.div
              key={selectedConv.key}
              initial={false}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={SPRING_FAST}
              className="space-y-3"
            >
              <p className="text-[11px] text-[#475569] pb-2 border-b border-[#1E2535]">
                {selectedConv.channel} · {selectedConv.conversation_id ?? 'no conversation id'} · {selectedConv.commands.length} {selectedConv.commands.length === 1 ? 'command' : 'commands'}
              </p>
              {selectedConv.commands.map(cmd => (
                <div key={cmd.command_id} className="flex gap-2.5">
                  <div className="p-1.5 rounded-lg flex-shrink-0 self-start mt-0.5 bg-[#1A1F2E]">
                    <User className="h-3.5 w-3.5 text-[#475569]" aria-hidden />
                  </div>
                  <div className="max-w-xs rounded-xl px-3 py-2 text-xs leading-relaxed bg-[#1A1F2E] border border-[#1E2535] text-[#94A3B8]">
                    {cmd.command}
                    <p className="text-[10px] text-[#475569] mt-1 flex items-center gap-1.5">
                      <span>{new Date(cmd.submitted_at).toLocaleTimeString()}</span>
                      <span className={STATUS_COLOR[cmd.status] ?? 'text-[#94A3B8]'}>{cmd.status}</span>
                      {cmd.goal_id && <span className="font-mono">goal {cmd.goal_id}</span>}
                    </p>
                    {cmd.error && <p className="text-[10px] text-red-400 mt-1">{cmd.error}</p>}
                  </div>
                </div>
              ))}
              <p className="text-[11px] text-[#475569] pt-2">
                Assistant replies are not recorded in command history — open the goal to see its result.
              </p>
            </motion.div>
          </AnimatePresence>
        )}
      </div>
    </JARVISPageShell>
  );
}

export default ConversationViewer;
