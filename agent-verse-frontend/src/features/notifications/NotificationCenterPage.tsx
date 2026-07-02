import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell, Plus, Trash2, Send, CheckCircle, XCircle,
  Webhook, MessageSquare, Zap, Settings, Eye, EyeOff,
} from "lucide-react";
import { notificationsApi } from "@/lib/api/client";
import type { NotificationChannel } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { toast } from "@/stores/toast";

// ── Types ──────────────────────────────────────────────────────────────────────

type ChannelType = "slack" | "webhook" | "teams";
type TestState = "idle" | "pending" | "success" | "error";

// ── Constants ──────────────────────────────────────────────────────────────────

const CHANNEL_TYPES: ChannelType[] = ["slack", "webhook", "teams"];

const CHANNEL_META: Record<ChannelType, { label: string; icon: React.ComponentType<{ className?: string }>; color: string }> = {
  slack:   { label: "Slack",   icon: MessageSquare, color: "text-purple-400" },
  webhook: { label: "Webhook", icon: Webhook,        color: "text-blue-400"   },
  teams:   { label: "Teams",   icon: Zap,            color: "text-indigo-400" },
};

const NOTIFICATION_EVENTS = [
  { id: "approval_required", label: "Approval Required", description: "A high-risk action is waiting for human sign-off before proceeding." },
  { id: "goal_complete",     label: "Goal Complete",     description: "An agent successfully completed its assigned goal."               },
  { id: "goal_failed",       label: "Goal Failed",       description: "A goal exhausted retries and could not reach completion."         },
] as const;

// ── Helpers ────────────────────────────────────────────────────────────────────

function buildConfig(type: ChannelType, fields: Record<string, string>): Record<string, unknown> {
  if (type === "slack" || type === "teams") return { webhook_url: fields.webhook_url };
  return { url: fields.url, auth_header: fields.auth_header || undefined, method: fields.method || "POST" };
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function StatCard({ label, value, subtext }: { label: string; value: string | number; subtext?: string }) {
  return (
    <div className="bg-card border border-border rounded-xl px-5 py-4">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</p>
      <p className="mt-1 text-2xl font-bold text-foreground">{value}</p>
      {subtext && <p className="text-xs text-muted-foreground mt-0.5">{subtext}</p>}
    </div>
  );
}

function ChannelFormFields({
  type, fields, onChange,
}: {
  type: ChannelType;
  fields: Record<string, string>;
  onChange: (key: string, val: string) => void;
}) {
  const input = "w-full px-3 py-2 text-sm bg-background border border-input rounded-lg focus:outline-none focus:ring-2 focus:ring-ring placeholder:text-muted-foreground";
  if (type === "slack") return (
    <div className="space-y-1.5">
      <label className="block text-xs font-medium text-muted-foreground">Incoming Webhook URL</label>
      <input type="url" className={input} placeholder="https://hooks.slack.com/services/…"
        value={fields.webhook_url ?? ""} onChange={(e) => onChange("webhook_url", e.target.value)} required />
      <p className="text-xs text-muted-foreground">Create one in Slack → App settings → Incoming Webhooks.</p>
    </div>
  );
  if (type === "teams") return (
    <div className="space-y-1.5">
      <label className="block text-xs font-medium text-muted-foreground">Incoming Webhook URL</label>
      <input type="url" className={input} placeholder="https://outlook.office.com/webhook/…"
        value={fields.webhook_url ?? ""} onChange={(e) => onChange("webhook_url", e.target.value)} required />
      <p className="text-xs text-muted-foreground">Create one via Connectors in your Teams channel settings.</p>
    </div>
  );
  return (
    <div className="space-y-2">
      <div className="space-y-1.5">
        <label className="block text-xs font-medium text-muted-foreground">Endpoint URL</label>
        <input type="url" className={input} placeholder="https://your-service.com/hooks/agentverse"
          value={fields.url ?? ""} onChange={(e) => onChange("url", e.target.value)} required />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1.5">
          <label className="block text-xs font-medium text-muted-foreground">Auth Header <span className="opacity-50">(optional)</span></label>
          <input type="text" className={input} placeholder="Bearer sk-…"
            value={fields.auth_header ?? ""} onChange={(e) => onChange("auth_header", e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <label className="block text-xs font-medium text-muted-foreground">HTTP Method</label>
          <select className={input} value={fields.method ?? "POST"} onChange={(e) => onChange("method", e.target.value)}>
            {["POST", "PUT", "PATCH"].map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      </div>
    </div>
  );
}

function ChannelCard({
  channel, localEnabled, onToggle, onDelete, onTest, testState, isDeleting,
}: {
  channel: NotificationChannel;
  localEnabled: boolean;
  onToggle: () => void;
  onDelete: () => void;
  onTest: () => void;
  testState: TestState;
  isDeleting: boolean;
}) {
  const meta = CHANNEL_META[channel.type as ChannelType];
  const Icon = meta?.icon ?? Bell;
  const colorClass = meta?.color ?? "text-muted-foreground";

  return (
    <div
      data-testid={`channel-item-${channel.channel_id}`}
      className={`flex items-center gap-4 bg-card border rounded-xl px-5 py-4 transition-all ${
        localEnabled ? "border-border" : "border-border/40 opacity-60"
      }`}
    >
      <div className={`flex-shrink-0 p-2 rounded-lg bg-muted ${colorClass}`}>
        <Icon className="h-4 w-4" aria-hidden="true" />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium capitalize">{meta?.label ?? channel.type}</span>
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border ${
            localEnabled
              ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
              : "bg-muted text-muted-foreground border-border"
          }`}>
            {localEnabled
              ? <><CheckCircle className="h-2.5 w-2.5" /> Active</>
              : <><XCircle className="h-2.5 w-2.5" /> Disabled</>}
          </span>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5 font-mono truncate">{channel.channel_id}</p>
      </div>

      {testState === "success" && (
        <span className="flex items-center gap-1 text-xs text-emerald-400 flex-shrink-0">
          <CheckCircle className="h-3.5 w-3.5" /> Delivered
        </span>
      )}
      {testState === "error" && (
        <span className="flex items-center gap-1 text-xs text-destructive flex-shrink-0">
          <XCircle className="h-3.5 w-3.5" /> Failed
        </span>
      )}

      <div className="flex items-center gap-1 flex-shrink-0">
        <button onClick={onToggle} aria-label={localEnabled ? "Disable channel" : "Enable channel"}
          title={localEnabled ? "Disable channel" : "Enable channel"}
          className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors">
          {localEnabled ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
        </button>
        <button data-testid={`test-btn-${channel.channel_id}`} onClick={onTest}
          disabled={testState === "pending"} aria-label="Send test notification"
          title="Send test notification"
          className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-primary transition-colors disabled:opacity-40">
          {testState === "pending"
            ? <Settings className="h-4 w-4 animate-spin" aria-hidden="true" />
            : <Send className="h-4 w-4" aria-hidden="true" />}
        </button>
        <button data-testid={`delete-btn-${channel.channel_id}`} onClick={onDelete}
          disabled={isDeleting} aria-label="Remove channel" title="Remove channel"
          className="p-2 rounded-lg hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors disabled:opacity-40">
          <Trash2 className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────────

export function NotificationCenterPage() {
  const qc = useQueryClient();

  const [showForm, setShowForm]         = useState(false);
  const [channelType, setChannelType]   = useState<ChannelType>("slack");
  const [formFields, setFormFields]     = useState<Record<string, string>>({});
  const [enabledMap, setEnabledMap]     = useState<Map<string, boolean>>(new Map());
  const [testStateMap, setTestStateMap] = useState<Map<string, TestState>>(new Map());

  const { data: channels = [], isLoading, error } = useQuery({
    queryKey: ["notification-channels"],
    queryFn: () => notificationsApi.list(),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      const primary = channelType === "webhook" ? formFields.url : formFields.webhook_url;
      if (!primary?.trim()) throw new Error("A webhook URL is required.");
      return notificationsApi.create({ channel_type: channelType, config: buildConfig(channelType, formFields) });
    },
    onSuccess: () => {
      toast({ kind: "success", message: "Channel added successfully." });
      qc.invalidateQueries({ queryKey: ["notification-channels"] });
      setShowForm(false);
      setFormFields({});
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => notificationsApi.delete(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Channel removed." });
      qc.invalidateQueries({ queryKey: ["notification-channels"] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const handleTest = async (channelId: string) => {
    setTestStateMap((p) => new Map(p).set(channelId, "pending"));
    try {
      const res = await notificationsApi.test(channelId);
      const next: TestState = res.success ? "success" : "error";
      setTestStateMap((p) => new Map(p).set(channelId, next));
      toast({ kind: res.success ? "success" : "error", message: res.message || (res.success ? "Test delivered." : "Test failed.") });
    } catch (e) {
      setTestStateMap((p) => new Map(p).set(channelId, "error"));
      toast({ kind: "error", message: String(e) });
    }
    setTimeout(() => setTestStateMap((p) => { const n = new Map(p); n.delete(channelId); return n; }), 3500);
  };

  const handleToggle = (ch: NotificationChannel) => {
    const cur = enabledMap.has(ch.channel_id) ? enabledMap.get(ch.channel_id)! : ch.enabled;
    setEnabledMap((p) => new Map(p).set(ch.channel_id, !cur));
  };

  const getEnabled = (ch: NotificationChannel) =>
    enabledMap.has(ch.channel_id) ? enabledMap.get(ch.channel_id)! : ch.enabled;

  const handleFieldChange = (key: string, val: string) =>
    setFormFields((p) => ({ ...p, [key]: val }));

  const handleTypeChange = (t: ChannelType) => { setChannelType(t); setFormFields({}); };

  const activeChannels = channels.filter(getEnabled).length;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Notification Center</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Route approval requests and goal updates to Slack, Teams, or a custom webhook.
          </p>
        </div>
        <button
          data-testid="add-channel-btn"
          onClick={() => setShowForm((s) => !s)}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 transition-opacity flex-shrink-0"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          Add channel
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Total Channels" value={isLoading ? "…" : channels.length} />
        <StatCard label="Active Channels" value={isLoading ? "…" : activeChannels} subtext="receiving events" />
        <StatCard label="Events Today" value="—" subtext="delivery log coming soon" />
      </div>

      {/* Add channel form */}
      {showForm && (
        <div data-testid="channel-form" className="bg-card border border-border rounded-xl p-5 space-y-4">
          <h2 className="text-sm font-semibold">New Notification Channel</h2>

          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-2">Channel type</label>
            <div className="flex gap-2" data-testid="channel-type-select">
              {CHANNEL_TYPES.map((t) => {
                const { label, icon: Icon } = CHANNEL_META[t];
                return (
                  <button key={t} onClick={() => handleTypeChange(t)}
                    className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium transition-all ${
                      channelType === t
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground"
                    }`}>
                    <Icon className="h-3.5 w-3.5" aria-hidden="true" />{label}
                  </button>
                );
              })}
            </div>
          </div>

          <ChannelFormFields type={channelType} fields={formFields} onChange={handleFieldChange} />

          <div className="flex gap-2 pt-1">
            <button onClick={() => createMutation.mutate()} disabled={createMutation.isPending}
              className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity">
              {createMutation.isPending ? "Adding…" : "Add channel"}
            </button>
            <button onClick={() => { setShowForm(false); setFormFields({}); }}
              className="px-4 py-2 border border-border text-muted-foreground rounded-lg text-sm hover:bg-muted transition-colors">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Channel list */}
      <div>
        <h2 className="text-sm font-semibold mb-3">Connected Channels</h2>
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-[72px] w-full rounded-xl" />)}
          </div>
        ) : error ? (
          <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-5 py-4 text-sm text-destructive">
            Failed to load channels: {String(error)}
          </div>
        ) : channels.length === 0 ? (
          <EmptyState
            title="No channels connected"
            description="Add a Slack, Teams, or custom webhook to receive approval requests and goal status updates."
            action={
              <button onClick={() => setShowForm(true)}
                className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90">
                <Plus className="h-4 w-4" aria-hidden="true" /> Add your first channel
              </button>
            }
          />
        ) : (
          <div className="space-y-2" data-testid="channel-list">
            {channels.map((c) => (
              <ChannelCard
                key={c.channel_id}
                channel={c}
                localEnabled={getEnabled(c)}
                onToggle={() => handleToggle(c)}
                onDelete={() => deleteMutation.mutate(c.channel_id)}
                onTest={() => handleTest(c.channel_id)}
                testState={testStateMap.get(c.channel_id) ?? "idle"}
                isDeleting={deleteMutation.isPending && deleteMutation.variables === c.channel_id}
              />
            ))}
          </div>
        )}
      </div>

      {/* Notification events */}
      <div>
        <h2 className="text-sm font-semibold mb-1">Notification Events</h2>
        <p className="text-xs text-muted-foreground mb-3">
          All connected channels receive the following events automatically.
        </p>
        <div className="divide-y divide-border border border-border rounded-xl overflow-hidden">
          {NOTIFICATION_EVENTS.map(({ id, label, description }) => (
            <div key={id} className="flex items-center gap-4 px-5 py-3.5 bg-card">
              <div className="flex-shrink-0 h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center">
                <Bell className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium">{label}</p>
                <p className="text-xs text-muted-foreground">{description}</p>
              </div>
              <span className="flex-shrink-0 px-2 py-0.5 text-xs rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
                Active
              </span>
            </div>
          ))}
        </div>
      </div>

    </div>
  );
}

export default NotificationCenterPage;
