/**
 * ConversationViewer — multi-turn conversation inspector across channels.
 * JARVIS motion: JARVISPageShell + JARVISStagger turn items.
 */
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  MessageSquare, Search, User, Cpu,
  MessageCircle, Hash, Terminal, Mail, Webhook, RefreshCw,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { Input } from '@/components/ui/input';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

interface ConversationTurn {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
}

interface Conversation {
  conversation_id: string;
  channel: string;
  actor_name?: string;
  turn_count: number;
  last_message: string;
  created_at: string;
  updated_at: string;
  turns?: ConversationTurn[];
}

const CHANNEL_ICON: Record<string, { icon: React.ElementType; color: string }> = {
  rest:     { icon: Terminal,      color: 'text-[#475569]'  },
  telegram: { icon: MessageCircle, color: 'text-blue-400'   },
  slack:    { icon: Hash,          color: 'text-purple-400' },
  email:    { icon: Mail,          color: 'text-yellow-400' },
  webhook:  { icon: Webhook,       color: 'text-orange-400' },
};

interface ConversationViewerProps { orgId: string; }

export function ConversationViewer({ orgId }: ConversationViewerProps) {
  const [search, setSearch]         = useState('');
  const [selected, setSelected]     = useState<string | null>(null);

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['conversations', orgId],
    queryFn: () =>
      apiFetch<any>(`/v1/org/${orgId}/commands?include_conversations=true`)
        .then(r => (Array.isArray(r) ? r : r?.conversations ?? r?.data ?? []))
        .catch(() => [] as Conversation[]),
    staleTime: 30_000,
  });

  const conversations: Conversation[] = data ?? [];
  const filtered = conversations.filter(c =>
    !search || c.last_message.toLowerCase().includes(search.toLowerCase())
      || (c.actor_name?.toLowerCase().includes(search.toLowerCase()) ?? false)
  );

  const selectedConv = conversations.find(c => c.conversation_id === selected);

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
            : filtered.length === 0
            ? <p className="text-xs text-[#475569] text-center py-8">No conversations yet.</p>
            : filtered.map(conv => {
              const chanConf = CHANNEL_ICON[conv.channel] ?? CHANNEL_ICON.rest;
              const ChanIcon = chanConf.icon;
              return (
                <button
                  key={conv.conversation_id}
                  onClick={() => setSelected(conv.conversation_id)}
                  role="listitem"
                  style={{ touchAction: 'manipulation' }}
                  className={cn(
                    'w-full text-left flex items-start gap-2.5 px-3 py-2.5 rounded-xl transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50',
                    selected === conv.conversation_id
                      ? 'bg-[#00D4FF]/10 border border-[#00D4FF]/20'
                      : 'hover:bg-[#1A1F2E] border border-transparent',
                  )}
                >
                  <ChanIcon className={cn('h-3.5 w-3.5 flex-shrink-0 mt-0.5', chanConf.color)} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-[#94A3B8] truncate">{conv.actor_name ?? conv.channel}</span>
                      <span className="text-[10px] text-[#475569] flex-shrink-0 ml-1">{conv.turn_count}t</span>
                    </div>
                    <p className="text-[11px] text-[#475569] truncate">{conv.last_message}</p>
                  </div>
                </button>
              );
            })
          }
        </div>
      </div>

      {/* ── Right: turns ── */}
      <div className="flex-1 h-full overflow-y-auto">
        {!selectedConv ? (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <MessageSquare className="h-12 w-12 text-[#1E2535]" aria-hidden />
            <p className="text-sm text-[#475569]">Select a conversation to inspect</p>
          </div>
        ) : (
          <AnimatePresence mode="wait">
            <motion.div
              key={selectedConv.conversation_id}
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              transition={SPRING_FAST}
              className="space-y-3"
            >
              <p className="text-[11px] text-[#475569] pb-2 border-b border-[#1E2535]">
                {selectedConv.channel} · {selectedConv.actor_name ?? 'Unknown'} · {selectedConv.turn_count} turns
              </p>
              {(selectedConv.turns ?? []).length === 0 ? (
                <div className="py-8 text-center">
                  <p className="text-xs text-[#475569]">Turn details not available (load full conversation).</p>
                </div>
              ) : (
                selectedConv.turns!.map((turn, i) => (
                  <div key={i} className={cn('flex gap-2.5', turn.role === 'assistant' && 'justify-end')}>
                    <div className={cn(
                      'p-1.5 rounded-lg flex-shrink-0 self-start mt-0.5',
                      turn.role === 'user' ? 'bg-[#1A1F2E]' : 'bg-[#00D4FF]/10',
                    )}>
                      {turn.role === 'user'
                        ? <User className="h-3.5 w-3.5 text-[#475569]" aria-hidden />
                        : <Cpu className="h-3.5 w-3.5 text-[#00D4FF]" aria-hidden />}
                    </div>
                    <div className={cn(
                      'max-w-xs rounded-xl px-3 py-2 text-xs leading-relaxed',
                      turn.role === 'user'
                        ? 'bg-[#1A1F2E] border border-[#1E2535] text-[#94A3B8]'
                        : 'bg-[#00D4FF]/5 border border-[#00D4FF]/20 text-[#94A3B8]',
                    )}>
                      {turn.content}
                      <p className="text-[10px] text-[#475569] mt-1">{new Date(turn.timestamp).toLocaleTimeString()}</p>
                    </div>
                  </div>
                ))
              )}
            </motion.div>
          </AnimatePresence>
        )}
      </div>
    </JARVISPageShell>
  );
}

export default ConversationViewer;
