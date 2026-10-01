import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy } from 'lucide-react';
import { API_BASE, integrationsApi, type SlackLinkCode } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { StatusBadge } from '@/components/ui/StatusBadge';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

interface ProviderConfig {
  name: string;
  description: string;
  endpoints: { label: string; path: string }[];
  secretEnv: string[];
}

const PROVIDERS: ProviderConfig[] = [
  {
    name: 'Slack',
    description: 'Slash command + interactive HITL buttons. Verifies requests with the signing secret.',
    endpoints: [
      { label: 'Slash command', path: '/integrations/slack/commands' },
      { label: 'Events', path: '/integrations/slack/events' },
      { label: 'Interactive', path: '/integrations/slack/interactive' },
    ],
    secretEnv: ['SLACK_SIGNING_SECRET'],
  },
  {
    name: 'Zapier',
    description: 'Inbound trigger to create goals; outbound polling trigger for completed goals.',
    endpoints: [
      { label: 'Trigger', path: '/integrations/zapier/trigger' },
      { label: 'Completed-goals poll', path: '/integrations/zapier/goals' },
    ],
    secretEnv: ['ZAPIER_SECRET', 'ZAPIER_TENANT_ID'],
  },
  {
    name: 'Alertmanager',
    description: 'Receives firing alerts and creates investigation goals.',
    endpoints: [{ label: 'Webhook', path: '/integrations/events/alertmanager' }],
    secretEnv: ['ALERTMANAGER_TENANT_ID'],
  },
  {
    name: 'Datadog',
    description: 'Receives critical/error events and creates goals; HMAC-verified when secret set.',
    endpoints: [{ label: 'Webhook', path: '/integrations/events/datadog' }],
    secretEnv: ['DATADOG_WEBHOOK_SECRET', 'DATADOG_TENANT_ID'],
  },
];

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    toast({ kind: 'success', message: 'Endpoint copied to clipboard.' });
  } catch {
    toast({ kind: 'error', message: 'Could not copy — copy it manually.' });
  }
}

export function IntegrationsPage() {
  const { data: zapierGoals = [] } = useQuery({
    queryKey: ['zapier-goals'],
    queryFn: () => integrationsApi.zapierCompletedGoals(),
  });
  const { data: slackWorkspaces = [], isError: slackWorkspacesError } = useQuery({
    queryKey: ['slack-workspaces'],
    queryFn: () => integrationsApi.slackWorkspaces(),
  });
  const routable = (s?: string) => s === 'verified' || s === 'legacy_unverified';

  // TRG-36: Slack users act only through a linked AgentVerse identity.
  const qc = useQueryClient();
  const [linkCode, setLinkCode] = useState<SlackLinkCode | null>(null);
  const { data: slackLinks = [], isError: slackLinksError } = useQuery({
    queryKey: ['slack-identities'],
    queryFn: () => integrationsApi.slackIdentities(),
  });
  const createCode = useMutation({
    mutationFn: () => integrationsApi.createSlackLinkCode(),
    onSuccess: (issued) => {
      setLinkCode(issued);
      qc.invalidateQueries({ queryKey: ['slack-identities'] });
    },
    onError: (err: unknown) =>
      toast({
        kind: 'error',
        message: err instanceof Error ? err.message : 'Could not create a Slack link code.',
      }),
  });
  const unlink = useMutation({
    mutationFn: (id: string) => integrationsApi.deleteSlackIdentity(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['slack-identities'] }),
    onError: (err: unknown) =>
      toast({
        kind: 'error',
        message: err instanceof Error ? err.message : 'Could not unlink the Slack account.',
      }),
  });
  const activeLinks = slackLinks.filter((l) => l.status === 'active');

  return (
    <JARVISPageShell className="bg-[#0A0F1A] min-h-screen">

      {/* a11y: live region for async updates */}
      <div aria-live="polite" aria-atomic="true" className="sr-only" />
    <JARVISStagger className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Integrations</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Inbound webhook endpoints. Point each provider at the URL below and set the listed env vars
          on the server.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {PROVIDERS.map((p) => (
          <div key={p.name} className="bg-card border border-border rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">{p.name}</h2>
              <StatusBadge status="running" />
            </div>
            <p className="text-sm text-muted-foreground">{p.description}</p>
            <div className="space-y-1.5">
              {p.endpoints.map((e) => {
                const fullUrl = `${API_BASE}${e.path}`;
                return (
                  <div key={e.path} className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground w-32 flex-shrink-0">
                      {e.label}
                    </span>
                    <code className="text-xs bg-muted rounded px-2 py-1 flex-1 truncate">
                      {e.path}
                    </code>
                    <button
                      aria-label={`Copy endpoint ${e.label}`}
                      onClick={() => copy(fullUrl)}
                      className="text-muted-foreground hover:text-foreground flex-shrink-0"
                    >
                      <Copy className="h-4 w-4" />
                    </button>
                  </div>
                );
              })}
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Required server env vars:</p>
              <div className="flex flex-wrap gap-1.5">
                {p.secretEnv.map((s) => (
                  <code key={s} className="text-xs bg-muted rounded px-2 py-0.5">
                    {s}
                  </code>
                ))}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* TRG-02: Slack commands and buttons act in the tenant bound to the workspace */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-sm">Slack — bound workspaces</h2>
          <p className="text-xs text-muted-foreground mt-1">
            /agentverse and approval buttons only work from a workspace verified under Channels.
          </p>
        </div>
        {slackWorkspacesError ? (
          <p className="px-5 py-4 text-sm text-red-400">Could not load Slack workspace bindings.</p>
        ) : slackWorkspaces.length === 0 ? (
          <p className="px-5 py-4 text-sm text-muted-foreground">
            No Slack workspace is bound to this tenant. Add your workspace (team ID) on the Channels
            page and verify it.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {slackWorkspaces.map((w) => (
              <li key={w.id} className="px-5 py-2 flex items-center justify-between gap-3">
                <code className="text-sm">{w.channel_id}</code>
                <span
                  className={`text-xs ${routable(w.status) ? 'text-green-400' : 'text-yellow-400'}`}
                >
                  {routable(w.status) ? 'bound' : `not routing (${w.status ?? 'unknown'})`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* TRG-36: link the caller's Slack account to its AgentVerse identity */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold text-sm">Slack — linked accounts</h2>
            <p className="text-xs text-muted-foreground mt-1">
              Approving from Slack needs an approver or admin key; submitting goals needs goal
              write access. Unlinked Slack users are refused.
            </p>
          </div>
          <button
            onClick={() => createCode.mutate()}
            disabled={createCode.isPending}
            className="text-xs border border-border rounded px-3 py-1.5 hover:bg-muted flex-shrink-0"
          >
            Link my Slack account
          </button>
        </div>
        {linkCode && (
          <div className="px-5 py-3 border-b border-border text-sm" role="status">
            In Slack, run <code className="bg-muted rounded px-1.5 py-0.5">/agentverse link {linkCode.code}</code>{' '}
            before {new Date(linkCode.expires_at).toLocaleTimeString()}.
          </div>
        )}
        {slackLinksError ? (
          <p className="px-5 py-4 text-sm text-red-400">Could not load linked Slack accounts.</p>
        ) : activeLinks.length === 0 ? (
          <p className="px-5 py-4 text-sm text-muted-foreground">No linked Slack accounts.</p>
        ) : (
          <ul className="divide-y divide-border">
            {activeLinks.map((l) => (
              <li key={l.id} className="px-5 py-2 flex items-center justify-between gap-3">
                <span className="text-sm">
                  <code>{l.slack_user_id}</code>{' '}
                  <span className="text-muted-foreground text-xs">
                    ({l.team_id}) as key {l.principal_id}
                  </span>
                </span>
                <button
                  aria-label={`Unlink ${l.slack_user_id ?? ''}`}
                  onClick={() => unlink.mutate(l.id)}
                  className="text-xs text-red-400 hover:underline"
                >
                  Unlink
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Zapier delivery visibility */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h2 className="font-semibold text-sm">Zapier — recent completed goals (poll payload)</h2>
        </div>
        {zapierGoals.length === 0 ? (
          <p className="px-5 py-4 text-sm text-muted-foreground">
            No completed goals available to the Zapier poll trigger.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {zapierGoals.map((g, i) => (
              <li
                key={g.goal_id ?? g.id ?? i}
                className="px-5 py-2 flex items-center justify-between gap-3"
              >
                <span className="text-sm truncate">{g.goal ?? g.goal_id ?? '—'}</span>
                <StatusBadge status={g.status} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
