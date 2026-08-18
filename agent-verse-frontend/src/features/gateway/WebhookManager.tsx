/**
 * WebhookManager — manage incoming webhooks with delivery logs.
 * JARVIS motion: JARVISPageShell + JARVISStagger.
 */
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Webhook, Plus, Trash2,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell, JARVISStagger, JARVISStaggerItem, SPRING_FAST } from '@/components/ui/JARVISPageShell';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

interface WebhookConfig {
  id: string;
  name: string;
  url: string;
  events: string[];
  secret?: string;
  active: boolean;
  last_delivery?: string;
  delivery_success_rate?: number;
}

function WebhookCard({
  webhook, onDelete,
}: { webhook: WebhookConfig; onDelete: (id: string) => void }) {
  const successRate = webhook.delivery_success_rate ?? 1;

  return (
    <motion.div
      layout
      className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4 hover:border-[#00D4FF]/20 transition-all"
    >
      <div className="flex items-start gap-3">
        <div className="p-2 rounded-lg bg-[#1A1F2E] flex-shrink-0">
          <Webhook className="h-4 w-4 text-orange-400" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2 mb-1">
            <span className="text-sm font-medium text-[#F1F5F9]">{webhook.name}</span>
            <div className="flex items-center gap-2">
              <span className={cn('h-2 w-2 rounded-full', webhook.active ? 'bg-emerald-400' : 'bg-[#475569]')} aria-label={webhook.active ? 'Active' : 'Inactive'} />
              <button
                onClick={() => onDelete(webhook.id)}
                aria-label={`Delete webhook ${webhook.name}`}
                style={{ touchAction: 'manipulation' }}
                className="p-1.5 rounded text-[#475569] hover:text-red-400 hover:bg-red-500/10 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/50"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
              </button>
            </div>
          </div>

          <p className="text-xs text-[#475569] truncate mb-2 font-mono">{webhook.url}</p>

          <div className="flex items-center gap-2 flex-wrap">
            {webhook.events.slice(0, 3).map(e => (
              <Badge key={e} variant="outline" className="text-[10px] border-[#1E2535] text-[#475569]">{e}</Badge>
            ))}
            {webhook.events.length > 3 && (
              <span className="text-[10px] text-[#475569]">+{webhook.events.length - 3} more</span>
            )}
          </div>

          {/* Delivery rate */}
          <div className="mt-2 flex items-center gap-2">
            <div className="h-1 w-20 rounded-full bg-[#1E2535] overflow-hidden">
              <div
                className={cn('h-full rounded-full', successRate >= 0.95 ? 'bg-emerald-400' : 'bg-yellow-400')}
                style={{ width: `${successRate * 100}%` }}
                aria-label={`${(successRate * 100).toFixed(0)}% success rate`}
              />
            </div>
            <span className="text-[10px] text-[#475569]">{(successRate * 100).toFixed(0)}% delivery</span>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

interface WebhookManagerProps { orgId: string; }

export function WebhookManager({ orgId }: WebhookManagerProps) {
  const qc = useQueryClient();
  const [showAdd, setShowAdd]   = useState(false);
  const [newName, setNewName]   = useState('');
  const [newUrl, setNewUrl]     = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['webhooks', orgId],
    queryFn: () =>
      apiFetch<any>(`/v1/org/${orgId}/gateway/webhooks`)
        .then(r => (Array.isArray(r) ? r : r?.data ?? []))
        .catch(() => [] as WebhookConfig[]),
    staleTime: 30_000,
  });

  const createWebhook = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/org/${orgId}/gateway/webhooks`, {
        method: 'POST',
        body: JSON.stringify({ name: newName, url: newUrl, events: ['*'] }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['webhooks', orgId] });
      setShowAdd(false);
      setNewName(''); setNewUrl('');
    },
  });

  const deleteWebhook = useMutation({
    mutationFn: (id: string) => apiFetch(`/v1/org/${orgId}/gateway/webhooks/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['webhooks', orgId] }),
  });

  const webhooks: WebhookConfig[] = data ?? [];

  return (
    <JARVISPageShell className="flex flex-col gap-5 max-w-2xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-base font-semibold text-[#F1F5F9] flex items-center gap-2">
            <Webhook className="h-4 w-4 text-orange-400" aria-hidden />
            Webhooks
          </h1>
          <p className="text-[11px] text-[#475569] mt-0.5">
            Receive org events at your endpoint. Signed with HMAC-SHA256.
          </p>
        </div>
        <button
          onClick={() => setShowAdd(v => !v)}
          style={{ touchAction: 'manipulation' }}
          className="flex items-center gap-2 px-3 py-2 rounded-xl bg-[#1A1F2E] border border-[#1E2535] text-sm text-[#94A3B8] hover:text-[#F1F5F9] hover:border-[#00D4FF]/30 transition-colors min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
        >
          <Plus className="h-4 w-4" aria-hidden />
          Add webhook
        </button>
      </div>

      {/* Add form */}
      <AnimatePresence>
        {showAdd && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={SPRING_FAST}
            style={{ overflow: 'hidden' }}
          >
            <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4 space-y-3">
              <div className="space-y-1.5">
                <label className="text-xs text-[#475569]">Webhook name</label>
                <Input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Deploy notifications" className="bg-[#1A1F2E] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569]" />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs text-[#475569]">Destination URL</label>
                <Input value={newUrl} onChange={e => setNewUrl(e.target.value)} placeholder="https://your-server.com/webhook" type="url" className="bg-[#1A1F2E] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569] font-mono" />
              </div>
              <button
                onClick={() => createWebhook.mutate()}
                disabled={createWebhook.isPending || !newName.trim() || !newUrl.trim()}
                style={{ touchAction: 'manipulation' }}
                className="px-4 py-2 rounded-lg bg-orange-600 text-white text-sm font-semibold hover:bg-orange-500 transition-colors disabled:opacity-40 min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-orange-400/50"
              >
                {createWebhook.isPending ? 'Creating…' : 'Create Webhook'}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Webhook list */}
      {isLoading ? (
        <div className="space-y-2">
          {[1, 2].map(i => <div key={i} className="h-28 rounded-xl bg-[#0F1623] animate-pulse" aria-hidden />)}
        </div>
      ) : webhooks.length === 0 ? (
        <div className="flex flex-col items-center py-12 gap-3">
          <Webhook className="h-10 w-10 text-[#1E2535]" aria-hidden />
          <p className="text-[#475569] text-sm">No webhooks configured.</p>
        </div>
      ) : (
        <JARVISStagger className="space-y-3">
          {webhooks.map(wh => (
            <JARVISStaggerItem key={wh.id}>
              <WebhookCard webhook={wh} onDelete={id => deleteWebhook.mutate(id)} />
            </JARVISStaggerItem>
          ))}
        </JARVISStagger>
      )}
    </JARVISPageShell>
  );
}

export default WebhookManager;
