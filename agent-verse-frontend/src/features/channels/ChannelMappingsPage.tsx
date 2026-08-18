import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';
import { MessageSquare, Plus, CheckCircle, AlertCircle } from 'lucide-react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

interface ChannelMapping {
  id: string;
  channel_type: string;
  channel_id: string;
  created_at?: string;
}

const CHANNEL_ICONS: Record<string, string> = {
  slack: '💬',
  teams: '💙',
  discord: '🎮',
  email: '📧',
  sms: '📱',
  voice: '🎙️',
  form: '📝',
};

export function ChannelMappingsPage() {
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [channelType, setChannelType] = useState('slack');
  const [channelId, setChannelId] = useState('');

  const { data: mappings = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['channel-mappings'],
    queryFn: () => apiFetch<ChannelMapping[]>('/channels/mappings'),
  });

  const createMapping = useMutation({
    mutationFn: (body: { channel_type: string; channel_id: string }) =>
      apiFetch('/channels/mappings', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['channel-mappings'] });
      setShowAdd(false);
      setChannelId('');
    },
  });

  return (
    <JARVISPageShell>
    <JARVISStagger className="flex flex-col gap-6 p-6 max-w-screen-lg mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <MessageSquare className="h-6 w-6 text-green-500" />
            Channel Connections
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Connect Slack, Teams, Discord, and other channels to enable conversational triggers.
          </p>
        </div>
        <button
          onClick={() => setShowAdd(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Add Channel
        </button>
      </div>

      {/* Error */}
      {isError && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive" role="alert">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Failed to load channel mappings.
          <button onClick={() => refetch()} className="ml-auto underline">Retry</button>
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="space-y-3">
          {[1, 2].map((i) => <div key={i} className="h-16 rounded-xl bg-muted animate-pulse" />)}
        </div>
      )}

      {/* Empty */}
      {!isLoading && !mappings.length && (
        <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
          <MessageSquare className="h-12 w-12 mb-4 opacity-20" />
          <p className="text-lg font-medium">No channels connected</p>
          <p className="text-sm mt-1">Connect a channel to enable conversational triggers.</p>
        </div>
      )}

      {/* Channel list */}
      {mappings.length > 0 && (
        <div className="rounded-xl border border-border bg-card overflow-hidden divide-y divide-border">
          {mappings.map((m) => (
            <div key={m.id} className="flex items-center gap-4 px-4 py-3">
              <span className="text-2xl" aria-label={m.channel_type}>
                {CHANNEL_ICONS[m.channel_type] ?? '🔗'}
              </span>
              <div className="flex-1">
                <div className="text-sm font-medium capitalize">{m.channel_type}</div>
                <div className="text-xs text-muted-foreground font-mono">{m.channel_id}</div>
              </div>
              <span className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                <CheckCircle className="h-3.5 w-3.5" />
                Connected
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Add channel modal */}
      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Add channel connection">
          <div className="absolute inset-0 bg-black/40" onClick={() => setShowAdd(false)} />
          <div className="relative w-full max-w-md rounded-xl bg-background shadow-xl p-6">
            <h2 className="text-lg font-semibold mb-4">Add Channel</h2>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-1.5">Channel Type</label>
                <select
                  value={channelType}
                  onChange={(e) => setChannelType(e.target.value)}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  {Object.keys(CHANNEL_ICONS).map((ct) => (
                    <option key={ct} value={ct}>{CHANNEL_ICONS[ct]} {ct.charAt(0).toUpperCase() + ct.slice(1)}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1.5">
                  {channelType === 'slack' ? 'Workspace ID' : channelType === 'email' ? 'Email Address' : 'Channel ID'}
                </label>
                <input
                  type="text"
                  value={channelId}
                  onChange={(e) => setChannelId(e.target.value)}
                  placeholder={channelType === 'slack' ? 'T12345ABCD' : channelType === 'email' ? 'support@company.com' : 'channel-id'}
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            </div>
            <div className="flex gap-2 justify-end mt-6">
              <button
                onClick={() => setShowAdd(false)}
                className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-[#1A1F2E] hover:shadow-glow-electric transition-[background-color,box-shadow]"
              >
                Cancel
              </button>
              <button
                onClick={() => createMapping.mutate({ channel_type: channelType, channel_id: channelId })}
                disabled={!channelId.trim() || createMapping.isPending}
                className="rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {createMapping.isPending ? 'Connecting…' : 'Connect Channel'}
              </button>
            </div>
          </div>
        </div>
      )}
    </JARVISStagger>
    </JARVISPageShell>
  );
}
