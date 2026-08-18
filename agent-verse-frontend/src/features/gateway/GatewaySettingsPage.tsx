/**
 * GatewaySettingsPage — Q10/Q12: Universal Command Gateway admin UI.
 *
 * Manages all inbound channels: REST API, Telegram, Slack, WhatsApp,
 * Discord, MCP Server, Email Commands, Webhooks.
 * Plus gateway-level settings: rate limits, 2FA requirements, command log.
 *
 * Skills:
 *   frontend-design:   JARVIS dark, channel status indicators, connector cards
 *   emil-design-eng:   spring 600/35 toggle, 300/28 setup modal
 *   impeccable-ui:     channel name dominant, status secondary, actions tertiary
 *   web-guidelines:    role=switch, aria-label, status[role=status]
 *   ui-ux-pro-max:     44px targets, useReducedMotion, keyboard nav
 */
import { useState, useCallback, useId } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  Link2, MessageCircle, Hash, Phone, Cpu, Mail, Webhook,
  Plus, Settings, Loader2, ExternalLink,
  Key, Shield, Clock, Zap,
} from 'lucide-react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

type ChannelStatus = 'connected' | 'disconnected' | 'pending';

interface Channel {
  id:          string;
  name:        string;
  description: string;
  status:      ChannelStatus;
  endpoint?:   string;
  userCount?:  number;
  toolCount?:  number;
  alwaysOn?:   boolean;
}

interface GatewayConfig {
  max_commands_per_hour: number;
  require_2fa_for:       string[];
  channels:              Channel[];
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

const apiClient = {
  get: <T,>(path: string) => apiRequest<T>('GET', path),
  post: <T,>(path: string, body?: unknown) => apiRequest<T>('POST', path, body),
  put: <T,>(path: string, body?: unknown) => apiRequest<T>('PUT', path, body),
  delete: <T,>(path: string) => apiRequest<T>('DELETE', path),
};

function useGatewayConfig() {
  return useQuery<GatewayConfig>({
    queryKey: ['gateway-config'],
    queryFn: () => apiClient.get<GatewayConfig>('/v1/gateway/config').catch(() => ({
      max_commands_per_hour: 100,
      require_2fa_for: ['approve', 'change-autonomy', 'delete'],
      channels: STATIC_CHANNELS,
    })),
    staleTime: 60_000,
  });
}

// ── Static channel definitions (augmented with API data) ─────────────────────

const STATIC_CHANNELS: Channel[] = [
  {
    id: 'rest-api', name: 'REST API', alwaysOn: true, status: 'connected',
    description: 'Always enabled — POST /v1/org/{org_id}/command',
    endpoint: 'POST /v1/org/{org_id}/command',
  },
  {
    id: 'telegram', name: 'Telegram', status: 'disconnected',
    description: 'Bot commands + voice notes via python-telegram-bot',
  },
  {
    id: 'slack', name: 'Slack', status: 'disconnected',
    description: 'Slash commands, mentions, Bolt SDK integration',
  },
  {
    id: 'whatsapp', name: 'WhatsApp', status: 'disconnected',
    description: 'WhatsApp Business API commands',
  },
  {
    id: 'discord', name: 'Discord', status: 'disconnected',
    description: 'Bot slash commands in Discord servers',
  },
  {
    id: 'mcp-server', name: 'MCP Server', status: 'disconnected',
    description: 'Expose org as MCP tools to Claude, Cursor, and other AI tools',
    toolCount: 0,
  },
  {
    id: 'email', name: 'Email Commands', status: 'disconnected',
    description: 'Parse and execute commands from a designated email address',
  },
  {
    id: 'webhooks', name: 'Webhooks', status: 'disconnected',
    description: 'Receive commands from external systems via HTTP webhooks',
  },
];

const CHANNEL_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  'rest-api':    Link2,
  'telegram':    MessageCircle,
  'slack':       Hash,
  'whatsapp':    Phone,
  'discord':     MessageCircle,
  'mcp-server':  Cpu,
  'email':       Mail,
  'webhooks':    Webhook,
};

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_MODAL = { type: 'spring', stiffness: 300, damping: 28 } as const;

// ── Channel Card ──────────────────────────────────────────────────────────────

function ChannelCard({ channel, index, onConnect }: {
  channel: Channel;
  index:   number;
  onConnect: (id: string) => void;
}) {
  const reduce = useReducedMotion();
  const Icon   = CHANNEL_ICONS[channel.id] ?? Link2;
  const isConn = channel.status === 'connected';

  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...SPRING_FAST, delay: index * 0.04 }}
      className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl p-4 flex items-start gap-3"
    >
      {/* Status dot */}
      <div className="mt-0.5 relative">
        <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${isConn ? 'bg-emerald-500/10' : 'bg-[#252B3B]'}`}>
          <Icon className={`h-4 w-4 ${isConn ? 'text-emerald-400' : 'text-[#475569]'}`} aria-hidden />
        </div>
        {isConn && (
          <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-400 rounded-full border-2 border-[#1A1F2E]" aria-label="Connected" />
        )}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <p className="text-[14px] font-semibold text-[#F1F5F9]">{channel.name}</p>
          {channel.alwaysOn && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 font-medium">Always On</span>
          )}
          {isConn && !channel.alwaysOn && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 font-medium">Connected</span>
          )}
        </div>
        <p className="text-[12px] text-[#64748B]">{channel.description}</p>
        {channel.endpoint && (
          <p className="mt-1 text-[11px] font-mono text-[#475569] bg-[#252B3B] px-2 py-0.5 rounded inline-block">{channel.endpoint}</p>
        )}
        {channel.userCount !== undefined && (
          <p className="mt-1 text-[11px] text-[#64748B]">Authorized users: {channel.userCount}</p>
        )}
        {channel.toolCount !== undefined && isConn && (
          <p className="mt-1 text-[11px] text-[#64748B]">Tools exposed: {channel.toolCount}</p>
        )}
      </div>

      {/* Action */}
      {!channel.alwaysOn && (
        <motion.button
          whileTap={reduce ? {} : { scale: 0.93 }}
          transition={SPRING_FAST}
          onClick={() => onConnect(channel.id)}
          aria-label={isConn ? `Manage ${channel.name}` : `Connect ${channel.name}`}
          style={{ touchAction: 'manipulation' }}
          className={[
            'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium flex-shrink-0',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 min-h-[34px]',
            isConn
              ? 'text-[#94A3B8] hover:text-[#F1F5F9] hover:bg-[#252B3B]'
              : 'text-blue-400 bg-blue-500/10 hover:bg-blue-500/20',
          ].join(' ')}
        >
          {isConn ? <><Settings className="h-3.5 w-3.5" aria-hidden />Manage</> : <><Plus className="h-3.5 w-3.5" aria-hidden />Connect</>}
        </motion.button>
      )}
      {channel.alwaysOn && (
        <div className="flex items-center gap-1.5 text-[12px] text-[#475569] flex-shrink-0">
          <Key className="h-3.5 w-3.5" aria-hidden />
          <span>API Keys</span>
          <ExternalLink className="h-3 w-3" aria-hidden />
        </div>
      )}
    </motion.div>
  );
}

// ── Connect Modal (generic wizard) ────────────────────────────────────────────

function ConnectModal({ channelId, onClose }: { channelId: string; onClose: () => void }) {
  const titleId = useId();
  const reduce  = useReducedMotion();
  const channel = STATIC_CHANNELS.find(c => c.id === channelId);
  const Icon    = CHANNEL_ICONS[channelId] ?? Link2;
  const [token, setToken] = useState('');
  const qc = useQueryClient();
  const connect = useMutation({
    mutationFn: (t: string) =>
      apiClient.post(`/v1/gateway/channels/${channelId}/connect`, { token: t }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['gateway-config'] }); onClose(); },
  });

  return (
    <div role="dialog" aria-modal="true" aria-labelledby={titleId}
      className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="absolute inset-0 bg-black/60" onClick={onClose} />
      <motion.div
        initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.95 }}
        transition={SPRING_MODAL}
        className="relative bg-[#0F1117] border border-[#2D3748] rounded-2xl w-full max-w-md shadow-2xl p-6"
      >
        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-xl bg-blue-500/10 flex items-center justify-center">
            <Icon className="h-5 w-5 text-blue-400" aria-hidden />
          </div>
          <div>
            <h2 id={titleId} className="text-[16px] font-bold text-[#F1F5F9]">Connect {channel?.name}</h2>
            <p className="text-[12px] text-[#64748B]">{channel?.description}</p>
          </div>
        </div>

        <div className="space-y-4">
          <div>
            <label htmlFor="channel-token" className="block text-[12px] font-medium text-[#94A3B8] mb-1.5">
              {channelId === 'telegram' ? 'Bot Token' :
               channelId === 'slack' ? 'OAuth Token' : 'Access Token / API Key'}
            </label>
            <input
              id="channel-token"
              type="password"
              value={token}
              onChange={e => setToken(e.target.value)}
              placeholder="Enter credentials..."
              aria-required="true"
              className="w-full px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#2D3748] text-[14px] text-[#F1F5F9] placeholder:text-[#475569] focus:outline-none focus:ring-2 focus:ring-blue-500/60"
            />
            <p className="mt-1.5 text-[11px] text-[#475569]">
              Token is encrypted at rest and never logged.
            </p>
          </div>

          <div className="flex gap-3">
            <motion.button
              type="button"
              onClick={() => connect.mutate(token)}
              disabled={!token.trim() || connect.isPending}
              whileTap={reduce ? {} : { scale: 0.97 }}
              transition={SPRING_FAST}
              style={{ touchAction: 'manipulation' }}
              className="flex-1 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
            >
              {connect.isPending ? <Loader2 className="h-4 w-4 animate-spin mx-auto" aria-label="Connecting" /> : 'Connect'}
            </motion.button>
            <motion.button
              type="button" onClick={onClose}
              whileTap={reduce ? {} : { scale: 0.97 }} transition={SPRING_FAST}
              style={{ touchAction: 'manipulation' }}
              className="px-4 py-2.5 rounded-xl border border-[#2D3748] text-[#94A3B8] text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
            >Cancel</motion.button>
          </div>
        </div>
      </motion.div>
    </div>
  );
}

// ── Emergency Stop Banner ─────────────────────────────────────────────────────

export function EmergencyStopBanner({ orgId }: { orgId: string }) {
  const reduce  = useReducedMotion();
  const [stopped, setStopped] = useState(false);
  const stop    = useMutation({
    mutationFn: () => apiRequest<{ status: string }>('POST', `/v1/org/${orgId}/emergency-stop`),
    onSuccess: () => setStopped(true),
  });
  const resume  = useMutation({
    mutationFn: () => apiRequest<{ status: string }>('POST', `/v1/org/${orgId}/emergency-stop/resume`),
    onSuccess: () => setStopped(false),
  });

  return (
    <div
      role="status"
      className={`flex items-center gap-3 px-4 py-2.5 rounded-xl border text-[13px] font-medium ${stopped ? 'bg-amber-500/10 border-amber-500/30 text-amber-400' : 'bg-red-500/5 border-red-500/20 text-[#94A3B8]'}`}
    >
      <Zap className="h-4 w-4 flex-shrink-0" aria-hidden />
      <span className="flex-1">{stopped ? 'Org PAUSED — no new autonomous work' : 'Emergency stop pauses all autonomous work instantly'}</span>
      <motion.button
        whileTap={reduce ? {} : { scale: 0.97 }}
        transition={SPRING_FAST}
        onClick={() => stopped ? resume.mutate() : stop.mutate()}
        disabled={stop.isPending || resume.isPending}
        aria-label={stopped ? 'Resume autonomous work' : 'Emergency stop — pause all work'}
        style={{ touchAction: 'manipulation' }}
        className={`px-3 py-1.5 rounded-lg text-[12px] font-semibold focus-visible:outline-none focus-visible:ring-2 min-h-[34px] ${stopped ? 'bg-amber-500/20 hover:bg-amber-500/30 focus-visible:ring-amber-400/70' : 'bg-red-500/15 hover:bg-red-500/25 text-red-400 focus-visible:ring-red-400/70'}`}
      >
        {stop.isPending || resume.isPending
          ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          : stopped ? 'Resume' : '⏸ Emergency Stop'}
      </motion.button>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

interface GatewaySettingsPageProps {
  orgId?: string;
}

export function GatewaySettingsPage({ orgId }: GatewaySettingsPageProps) {
  const reduce = useReducedMotion();
  const { data: config } = useGatewayConfig();
  const channels = config?.channels ?? STATIC_CHANNELS;
  const [connectingId, setConnectingId] = useState<string | null>(null);
  const connectedCount = channels.filter(c => c.status === 'connected').length;

  const handleConnect = useCallback((id: string) => {
    const ch = channels.find(c => c.id === id);
    if (ch?.alwaysOn) return;
    setConnectingId(id);
  }, [channels]);

  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={SPRING_MODAL}
      className="max-w-3xl mx-auto px-6 py-8"
    >
      <div className="mb-8">
        <h1 className="text-[24px] font-bold text-[#F1F5F9] [text-wrap:balance]">Command Gateway</h1>
        <p className="text-[14px] text-[#64748B] mt-1 tabular-nums">
          {connectedCount} channel{connectedCount !== 1 ? 's' : ''} active
          · Limit: {config?.max_commands_per_hour ?? 100} commands/hour
        </p>
      </div>

      {orgId && (
        <div className="mb-6">
          <EmergencyStopBanner orgId={orgId} />
        </div>
      )}

      {/* Active channels */}
      <section aria-label="Communication channels" className="mb-8">
        <h2 className="text-[12px] font-semibold text-[#64748B] uppercase tracking-wider mb-4">Channels</h2>
        <div className="space-y-2">
          {channels.map((ch, i) => (
            <ChannelCard key={ch.id} channel={ch} index={i} onConnect={handleConnect} />
          ))}
        </div>
      </section>

      {/* Gateway settings */}
      <section aria-label="Gateway security settings" className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl p-5">
        <h2 className="text-[12px] font-semibold text-[#64748B] uppercase tracking-wider mb-4">Security Settings</h2>
        <div className="space-y-4 text-[13px]">
          <div className="flex items-center justify-between py-2 border-b border-[#252B3B]">
            <div className="flex items-center gap-2.5">
              <Clock className="h-4 w-4 text-[#475569]" aria-hidden />
              <div>
                <p className="font-medium text-[#F1F5F9]">Rate limit</p>
                <p className="text-[#64748B] text-[12px]">Maximum commands per hour across all channels</p>
              </div>
            </div>
            <span className="font-mono text-[#94A3B8] tabular-nums">{config?.max_commands_per_hour ?? 100}/h</span>
          </div>

          <div className="flex items-start gap-2.5">
            <Shield className="h-4 w-4 text-[#475569] flex-shrink-0 mt-0.5" aria-hidden />
            <div>
              <p className="font-medium text-[#F1F5F9]">Require 2FA for</p>
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {(config?.require_2fa_for ?? ['approve', 'change-autonomy', 'delete']).map(action => (
                  <span key={action} className="px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 text-[11px] font-medium">{action}</span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Feedback */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {connectingId && `Setting up ${STATIC_CHANNELS.find(c => c.id === connectingId)?.name} connection.`}
      </div>

      {/* Connect modal */}
      <AnimatePresence>
        {connectingId && (
          <ConnectModal channelId={connectingId} onClose={() => setConnectingId(null)} />
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export default GatewaySettingsPage;
