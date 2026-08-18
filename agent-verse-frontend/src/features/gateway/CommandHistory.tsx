/**
 * CommandHistory — log of all commands sent via any channel.
 *
 * JARVIS motion: JARVISPageShell + JARVISStagger items.
 */
import { useState } from 'react';
import { motion } from 'framer-motion';
import {
  Terminal, MessageCircle, Hash, Mail, Webhook,
  CheckCircle2, Clock, AlertTriangle, Search, RefreshCw,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem } from '@/components/ui/JARVISPageShell';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

// ── Types ──────────────────────────────────────────────────────────────────

interface CommandEntry {
  command_id: string;
  command: string;
  channel: string;
  actor_name?: string;
  status: string;
  response_text?: string;
  created_at: string;
  completed_at?: string;
}

// ── Channel icons ──────────────────────────────────────────────────────────

const CHANNEL_ICON: Record<string, { icon: React.ElementType; color: string }> = {
  rest:     { icon: Terminal,     color: 'text-[#475569]'   },
  telegram: { icon: MessageCircle,color: 'text-blue-400'    },
  slack:    { icon: Hash,         color: 'text-purple-400'  },
  email:    { icon: Mail,         color: 'text-yellow-400'  },
  webhook:  { icon: Webhook,      color: 'text-orange-400'  },
};

const STATUS_CONFIG: Record<string, { icon: React.ElementType; class: string }> = {
  completed:  { icon: CheckCircle2, class: 'text-emerald-400' },
  processing: { icon: Clock,        class: 'text-yellow-400'  },
  failed:     { icon: AlertTriangle,class: 'text-red-400'     },
};

function CommandRow({ cmd }: { cmd: CommandEntry }) {
  const chanConf   = CHANNEL_ICON[cmd.channel] ?? CHANNEL_ICON.rest;
  const statusConf = STATUS_CONFIG[cmd.status] ?? STATUS_CONFIG.completed;
  const ChanIcon   = chanConf.icon;
  const StatusIcon = statusConf.icon;

  return (
    <motion.div
      layout
      className="flex items-start gap-3 px-4 py-3 rounded-xl bg-[#0F1623] border border-[#1E2535] hover:border-[#00D4FF]/20 hover:shadow-glow-electric transition-all"
      role="listitem"
    >
      <div className="p-1.5 rounded-lg bg-[#1A1F2E] flex-shrink-0 mt-0.5">
        <ChanIcon className={cn('h-3.5 w-3.5', chanConf.color)} aria-hidden />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-[#F1F5F9] truncate">{cmd.command}</p>
        {cmd.response_text && (
          <p className="text-xs text-[#475569] truncate mt-0.5">→ {cmd.response_text}</p>
        )}
        <div className="flex items-center gap-2 mt-1.5">
          <Badge variant="outline" className="text-[10px] border-[#1E2535] text-[#475569] capitalize">
            {cmd.channel}
          </Badge>
          {cmd.actor_name && (
            <span className="text-[10px] text-[#475569]">{cmd.actor_name}</span>
          )}
          <span className="text-[10px] text-[#475569] ml-auto">
            {new Date(cmd.created_at).toLocaleTimeString()}
          </span>
        </div>
      </div>
      <StatusIcon className={cn('h-3.5 w-3.5 flex-shrink-0 mt-1', statusConf.class)} aria-label={cmd.status} />
    </motion.div>
  );
}

interface CommandHistoryProps {
  orgId: string;
}

export function CommandHistory({ orgId }: CommandHistoryProps) {
  const [search, setSearch] = useState('');
  const [channelFilter, setChannelFilter] = useState('all');

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['command-history', orgId],
    queryFn: () =>
      apiFetch<any>(`/v1/org/${orgId}/commands`)
        .then(r => (Array.isArray(r) ? r : r?.data ?? r?.items ?? []))
        .catch(() => [] as CommandEntry[]),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });

  const items: CommandEntry[] = data ?? [];
  const filtered = items.filter(c => {
    const matchesSearch  = !search || c.command.toLowerCase().includes(search.toLowerCase());
    const matchesChannel = channelFilter === 'all' || c.channel === channelFilter;
    return matchesSearch && matchesChannel;
  });

  const channels = ['all', ...Array.from(new Set(items.map(c => c.channel)))];

  return (
    <JARVISPageShell className="flex flex-col gap-4 h-full">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <h2 className="text-base font-semibold text-[#F1F5F9] flex items-center gap-2">
          <Terminal className="h-4 w-4 text-[#00D4FF]" aria-hidden />
          Command History
        </h2>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          aria-label="Refresh history"
          style={{ touchAction: 'manipulation' }}
          className="p-2 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-[#1A1F2E] transition-colors min-w-[44px] min-h-[44px] flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
        >
          <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} aria-hidden />
        </button>
      </div>

      {/* Filters */}
      <div className="flex gap-2 shrink-0">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#475569]" aria-hidden />
          <Input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search commands…"
            className="pl-9 bg-[#0F1623] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569] text-xs h-9"
            aria-label="Search commands"
          />
        </div>
        <select
          value={channelFilter}
          onChange={e => setChannelFilter(e.target.value)}
          className="bg-[#0F1623] border border-[#1E2535] text-[#94A3B8] text-xs rounded-lg px-2 h-9 focus:outline-none focus:ring-2 focus:ring-[#00D4FF]/50"
          aria-label="Filter by channel"
        >
          {channels.map(c => <option key={c} value={c}>{c === 'all' ? 'All channels' : c}</option>)}
        </select>
      </div>

      {/* Count */}
      <p className="text-[11px] text-[#475569] shrink-0" aria-live="polite">
        {filtered.length} command{filtered.length !== 1 ? 's' : ''}
      </p>

      {/* List */}
      <div className="flex-1 overflow-y-auto" role="list" aria-label="Command history">
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3].map(i => <div key={i} className="h-16 rounded-xl bg-[#0F1623] animate-pulse" aria-hidden />)}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center py-12 gap-3">
            <Terminal className="h-10 w-10 text-[#1E2535]" aria-hidden />
            <p className="text-[#475569] text-sm">
              {search ? 'No matching commands.' : 'No commands yet. Send your first command via any channel.'}
            </p>
          </div>
        ) : (
          <JARVISStagger className="space-y-2">
            {filtered.map(cmd => (
              <JARVISStaggerItem key={cmd.command_id}>
                <CommandRow cmd={cmd} />
              </JARVISStaggerItem>
            ))}
          </JARVISStagger>
        )}
      </div>
    </JARVISPageShell>
  );
}

export default CommandHistory;
