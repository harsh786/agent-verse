/**
 * CommandHistoryPanel — Q2/Q3: UCG command log with live status tracking.
 *
 * Shows all commands sent via any channel (REST, Telegram, Slack, etc.)
 * with status tracking, high-risk flags, and routing confirmations.
 *
 * Skills:
 *   frontend-design:   JARVIS dark, channel color badges, status timeline
 *   emil-design-eng:   spring 280/26 row entrance, stagger 40ms
 *   impeccable-ui:     command text dominant, status secondary, channel tertiary
 *   web-guidelines:    aria-live for new commands, time[datetime], status indicators
 *   ui-ux-pro-max:     44px submit, useReducedMotion, keyboard nav
 */
import { useState, useCallback } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Terminal, Send, AlertTriangle, CheckCircle2, Loader2, Clock, Filter } from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

type CommandStatus = 'queued' | 'routed' | 'routing_failed' | 'pending_2fa' | 'complete' | 'failed';

interface OrgCommand {
  command_id:    string;
  command:       string;
  channel:       string;
  status:        CommandStatus;
  requires_2fa:  boolean;
  submitted_at:  string;
  result?:       unknown;
  error?:        string;
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useCommands(orgId: string, channel?: string) {
  return useQuery<{ commands: OrgCommand[]; total: number }>({
    queryKey: ['org-commands', orgId, channel],
    queryFn: () => apiRequest('GET', `/v1/org/${orgId}/commands${channel ? `?channel=${channel}` : ''}`),
    refetchInterval: 5000,
    staleTime: 3000,
  });
}

function useSendCommand(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { command: string; channel: string }) =>
      apiRequest('POST', `/v1/org/${orgId}/command`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['org-commands', orgId] }),
  });
}

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_ROW  = { type: 'spring', stiffness: 280, damping: 26 } as const;

// ── Channel badge ─────────────────────────────────────────────────────────────

const CHANNEL_COLORS: Record<string, string> = {
  rest:      'bg-blue-500/10 text-blue-400',
  telegram:  'bg-sky-500/10 text-sky-400',
  slack:     'bg-purple-500/10 text-purple-400',
  whatsapp:  'bg-emerald-500/10 text-emerald-400',
  discord:   'bg-indigo-500/10 text-indigo-400',
  email:     'bg-amber-500/10 text-amber-400',
  mcp:       'bg-violet-500/10 text-violet-400',
};

function ChannelBadge({ channel }: { channel: string }) {
  const cls = CHANNEL_COLORS[channel] ?? 'bg-[#252B3B] text-[#64748B]';
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium flex-shrink-0 ${cls}`}>
      {channel}
    </span>
  );
}

// ── Status icon ───────────────────────────────────────────────────────────────

function StatusIcon({ status }: { status: CommandStatus }) {
  if (status === 'routed' || status === 'complete')
    return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 flex-shrink-0" aria-label="Complete" />;
  if (status === 'routing_failed' || status === 'failed')
    return <AlertTriangle className="h-3.5 w-3.5 text-red-400 flex-shrink-0" aria-label="Failed" />;
  if (status === 'pending_2fa')
    return <AlertTriangle className="h-3.5 w-3.5 text-amber-400 flex-shrink-0" aria-label="Pending 2FA" />;
  if (status === 'queued')
    return <Clock className="h-3.5 w-3.5 text-[#64748B] flex-shrink-0 animate-pulse" aria-label="Queued" />;
  return <Loader2 className="h-3.5 w-3.5 text-blue-400 flex-shrink-0 animate-spin" aria-label="Processing" />;
}

// ── Command row ───────────────────────────────────────────────────────────────

function CommandRow({ cmd, index }: { cmd: OrgCommand; index: number }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...SPRING_ROW, delay: index * 0.04 }}
      className="flex items-start gap-2.5 p-3 bg-[#1A1F2E] border border-[#2D3748] rounded-xl"
    >
      <StatusIcon status={cmd.status} />
      <div className="flex-1 min-w-0">
        <p className="text-[13px] text-[#F1F5F9] truncate font-medium">{cmd.command}</p>
        <div className="flex items-center gap-2 mt-1">
          <time dateTime={cmd.submitted_at} className="text-[10px] text-[#475569] tabular-nums">
            {new Intl.DateTimeFormat('en', { timeStyle: 'short' }).format(new Date(cmd.submitted_at))}
          </time>
          <span className="text-[10px] text-[#374151]">·</span>
          <span className={`text-[10px] font-medium ${
            cmd.status === 'routed' ? 'text-emerald-400' :
            cmd.status === 'routing_failed' || cmd.status === 'failed' ? 'text-red-400' :
            cmd.status === 'pending_2fa' ? 'text-amber-400' : 'text-[#64748B]'
          }`}>{cmd.status.replace('_', ' ')}</span>
          {cmd.requires_2fa && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-400">2FA</span>
          )}
        </div>
        {cmd.error && (
          <p className="text-[11px] text-red-400 mt-1 truncate">{cmd.error}</p>
        )}
      </div>
      <ChannelBadge channel={cmd.channel} />
    </motion.div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────────

interface CommandHistoryPanelProps {
  orgId: string;
}

export function CommandHistoryPanel({ orgId }: CommandHistoryPanelProps) {
  const reduce = useReducedMotion();
  const [input, setInput]     = useState('');
  const [channel, setChannel] = useState('rest');
  const [filter, setFilter]   = useState<string | undefined>();
  const { data, isLoading }   = useCommands(orgId, filter);
  const send                  = useSendCommand(orgId);
  const commands              = data?.commands ?? [];

  const handleSend = useCallback(async () => {
    if (!input.trim()) return;
    await send.mutateAsync({ command: input.trim(), channel });
    setInput('');
  }, [send, input, channel]);

  return (
    <section aria-label="Command Gateway history" className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-emerald-400" aria-hidden />
          <span className="text-[13px] font-semibold text-[#F1F5F9]">Command Gateway</span>
          {data && (
            <span className="text-[11px] text-[#64748B] tabular-nums">{data.total} total</span>
          )}
        </div>
        {/* Channel filter */}
        <div className="flex items-center gap-1.5">
          <Filter className="h-3 w-3 text-[#475569]" aria-hidden />
          <select
            value={filter ?? ''}
            onChange={e => setFilter(e.target.value || undefined)}
            aria-label="Filter by channel"
            className="text-[11px] bg-transparent text-[#64748B] border-none focus:outline-none"
          >
            <option value="">All channels</option>
            {['rest','telegram','slack','whatsapp','discord','email','mcp'].map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Send command form */}
      <div className="flex gap-2">
        <div className="flex-1 flex items-stretch bg-[#1A1F2E] border border-[#2D3748] rounded-xl overflow-hidden">
          <select
            value={channel}
            onChange={e => setChannel(e.target.value)}
            aria-label="Channel"
            className="bg-transparent text-[11px] text-[#64748B] border-r border-[#2D3748] px-2.5 focus:outline-none"
          >
            {['rest','telegram','slack','whatsapp','discord','email','mcp'].map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
            placeholder="Send a command…"
            aria-label="Command to send"
            className="flex-1 px-3 py-2.5 bg-transparent text-[13px] text-[#F1F5F9] placeholder:text-[#374151] focus:outline-none"
          />
        </div>
        <motion.button
          whileTap={reduce ? {} : { scale: 0.93 }} transition={SPRING_FAST}
          onClick={handleSend}
          disabled={!input.trim() || send.isPending}
          aria-label="Send command"
          style={{ touchAction: 'manipulation' }}
          className="w-10 h-10 rounded-xl bg-blue-600 hover:bg-blue-500 flex items-center justify-center text-white disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
        >
          {send.isPending
            ? <Loader2 className="h-4 w-4 animate-spin" aria-label="Sending" />
            : <Send className="h-4 w-4" aria-hidden />
          }
        </motion.button>
      </div>

      {/* Command list */}
      <div aria-live="polite" className="space-y-2">
        {isLoading ? (
          <div className="flex items-center gap-2 py-6 justify-center text-[#475569] text-[12px]">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />Loading commands…
          </div>
        ) : commands.length === 0 ? (
          <div className="text-center py-8 text-[#475569] text-[13px]">
            <Terminal className="h-8 w-8 mx-auto mb-2 opacity-20" aria-hidden />
            No commands yet. Send the first one above.
          </div>
        ) : (
          <AnimatePresence mode="popLayout">
            {commands.map((cmd, i) => (
              <CommandRow key={cmd.command_id} cmd={cmd} index={i} />
            ))}
          </AnimatePresence>
        )}
      </div>

      {send.isSuccess && (
        <div aria-live="assertive" aria-atomic="true" className="sr-only">Command sent successfully.</div>
      )}
    </section>
  );
}

export default CommandHistoryPanel;
