import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { notificationsApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";

const CHANNEL_TYPES = ["slack", "webhook", "teams"] as const;

export function NotificationCenterPage() {
  const qc = useQueryClient();
  const [channelType, setChannelType] = useState<(typeof CHANNEL_TYPES)[number]>("webhook");
  const [configText, setConfigText] = useState('{ "url": "" }');

  const { data: channels = [], isLoading, error } = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => notificationsApi.list(),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      let config: Record<string, unknown> = {};
      try {
        config = JSON.parse(configText) as Record<string, unknown>;
      } catch {
        throw new Error("Config must be valid JSON");
      }
      return notificationsApi.create({ channel_type: channelType, config });
    },
    onSuccess: () => {
      toast({ kind: "success", message: "Channel created." });
      qc.invalidateQueries({ queryKey: ["notification-channels"] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => notificationsApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notification-channels"] }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Notification Center</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Configure where approval requests and goal updates are delivered.
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 space-y-3">
        <h2 className="font-semibold text-sm">Add a channel</h2>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Type
            <select
              value={channelType}
              onChange={(e) => setChannelType(e.target.value as (typeof CHANNEL_TYPES)[number])}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            >
              {CHANNEL_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground flex-1 min-w-[16rem]">
            Config (JSON)
            <input
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm font-mono"
            />
          </label>
          <button
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Add channel
          </button>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
        </div>
      ) : error ? (
        <div className="text-sm text-destructive">Failed to load channels: {String(error)}</div>
      ) : channels.length === 0 ? (
        <EmptyState title="No channels" description="Add a Slack, webhook, or Teams channel to receive alerts." />
      ) : (
        <div className="space-y-2">
          {channels.map((c) => (
            <div key={c.channel_id} className="flex items-center justify-between bg-card border border-border rounded-lg px-4 py-3">
              <div className="text-sm">
                <span className="font-medium">{c.type}</span>
                <span className="ml-2 font-mono text-xs text-muted-foreground">{c.channel_id}</span>
                {!c.enabled && <span className="ml-2 text-xs text-muted-foreground">(disabled)</span>}
              </div>
              <button
                onClick={() => deleteMutation.mutate(c.channel_id)}
                aria-label={`Delete channel ${c.channel_id}`}
                className="p-1.5 rounded-md hover:bg-accent text-muted-foreground"
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="bg-muted/40 border border-border rounded-xl p-4 text-sm text-muted-foreground">
        Delivery logs are not yet available. Channel delivery history will appear here once the
        backend records per-channel delivery events.
      </div>
    </div>
  );
}

export default NotificationCenterPage;
