/**
 * WorkflowSettingsPage — 6-panel settings for a workflow definition.
 *
 * Panels: General | Permissions | Secrets | Environment | Notifications | Webhook
 */
import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ChevronLeft, Settings, Lock, Key, Sliders, Bell, Globe, Loader2,
} from 'lucide-react';
import { workflowEngineApi, type WEWorkflow } from '../../lib/api/client';
import {
  ACCESS_LEVELS, hasWorkflowAccess, workflowErrorMessage, type WorkflowAccess,
} from './access';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

// ── Panel selector ────────────────────────────────────────────────────────────

const PANELS = [
  { id: 'general',       label: 'General',       icon: Settings },
  { id: 'permissions',   label: 'Permissions',   icon: Lock },
  { id: 'secrets',       label: 'Secrets',       icon: Key },
  { id: 'env',           label: 'Environment',   icon: Sliders },
  { id: 'notifications', label: 'Notifications', icon: Bell },
  { id: 'webhook',       label: 'Webhook',       icon: Globe },
] as const;

type PanelId = typeof PANELS[number]['id'];

// ── Panel components ──────────────────────────────────────────────────────────

function GeneralPanel({ wf }: { wf: WEWorkflow }) {
  const qc = useQueryClient();
  const [name, setName] = useState(wf.name);
  const [description, setDescription] = useState(wf.description);
  const [retention, setRetention] = useState(90);

  const saveMutation = useMutation({
    mutationFn: () => workflowEngineApi.update(wf.id, { name, description }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workflow-engine', 'get', wf.id] }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-[#F1F5F9] mb-4">General Settings</h3>
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-[#F1F5F9]/50 mb-1" htmlFor="wf-name">
              Workflow Name
            </label>
            <input
              id="wf-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 rounded-xl bg-[#0F1826]/5 border border-white/10 text-[#F1F5F9]
                         text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-[#F1F5F9]/50 mb-1" htmlFor="wf-desc">
              Description
            </label>
            <textarea
              id="wf-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 rounded-xl bg-[#0F1826]/5 border border-white/10 text-[#F1F5F9]
                         text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 resize-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-[#F1F5F9]/50 mb-1" htmlFor="retention">
              Run Retention (days)
            </label>
            <input
              id="retention"
              type="number"
              value={retention}
              onChange={(e) => setRetention(Number(e.target.value))}
              min={1}
              max={365}
              className="w-32 px-3 py-2 rounded-xl bg-[#0F1826]/5 border border-white/10 text-[#F1F5F9]
                         text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
            />
          </div>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500
                       text-[#F1F5F9] text-sm font-medium transition-colors disabled:opacity-60"
          >
            {saveMutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save Changes
          </button>
        </div>
      </div>
    </div>
  );
}

function PermissionsPanel({ wf }: { wf: WEWorkflow }) {
  const qc = useQueryClient();
  const queryKey = ['workflow-engine', 'permissions', wf.id];
  const [subject, setSubject] = useState('');
  const [subjectType, setSubjectType] = useState<'user' | 'role'>('user');
  const [level, setLevel] = useState<WorkflowAccess>('viewer');
  const [error, setError] = useState<string | null>(null);
  const canManage = hasWorkflowAccess(wf.access, 'admin');

  const { data: grants, isLoading, error: loadError } = useQuery({
    queryKey,
    queryFn: () => workflowEngineApi.listPermissions(wf.id),
  });

  const onError = (action: string) => (err: unknown) => setError(workflowErrorMessage(err, action));
  const addMutation = useMutation({
    mutationFn: () =>
      workflowEngineApi.addPermission(wf.id, {
        subject: subject.trim(), role: level, subject_type: subjectType,
      }),
    onMutate: () => setError(null),
    onSuccess: () => {
      setSubject('');
      qc.invalidateQueries({ queryKey });
    },
    onError: onError('change access to'),
  });
  const removeMutation = useMutation({
    mutationFn: (permissionId: string) => workflowEngineApi.removePermission(wf.id, permissionId),
    onMutate: () => setError(null),
    onSuccess: () => qc.invalidateQueries({ queryKey }),
    onError: onError('change access to'),
  });

  return (
    <div>
      <h3 className="text-sm font-semibold text-[#F1F5F9] mb-4">Access Control</h3>
      <div className="rounded-xl border border-white/10 p-4 mb-4">
        <p className="text-xs text-[#F1F5F9]/50 mb-2">
          With no grants, everyone in your organization has full access. Once any grant
          exists, only listed keys or roles (and organization admins) can use this workflow.
        </p>
        <ul className="space-y-1" aria-label="Access levels">
          {ACCESS_LEVELS.map((l) => (
            <li key={l.id} className="text-xs text-[#F1F5F9]/40">
              <span className="font-medium text-[#F1F5F9]/70">{l.label}</span> — {l.description}
            </li>
          ))}
        </ul>
        {wf.access && (
          <p className="text-xs text-[#F1F5F9]/40 mt-2">
            Your access: <span className="font-medium text-sky-400">{wf.access}</span>
          </p>
        )}
      </div>

      <div className="rounded-xl border border-white/10 divide-y divide-white/5 mb-4">
        {isLoading ? (
          <div className="p-4 text-xs text-[#F1F5F9]/40">Loading permissions…</div>
        ) : loadError ? (
          <div className="p-4 text-xs text-red-400" role="alert">
            {workflowErrorMessage(loadError, 'view access for')}
          </div>
        ) : !grants?.length ? (
          <div className="p-4 text-xs text-[#F1F5F9]/40">No grants — organization-wide access.</div>
        ) : (
          grants.map((g) => (
            <div key={g.id} className="p-3 flex items-center gap-3 text-xs" data-testid="grant">
              <span className="font-mono text-[#F1F5F9]/80 truncate">
                {g.subject_type === 'role' ? `role: ${g.subject_id}` : g.subject_id}
              </span>
              <span className="px-2 py-0.5 rounded-full bg-sky-500/15 text-sky-400">{g.permission}</span>
              <button
                onClick={() => removeMutation.mutate(g.id)}
                disabled={!canManage || removeMutation.isPending}
                className="ml-auto text-red-400/70 hover:text-red-400 disabled:opacity-40"
                aria-label={`Remove ${g.permission} access for ${g.subject_id}`}
              >
                Remove
              </button>
            </div>
          ))
        )}
      </div>

      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (subject.trim()) addMutation.mutate();
        }}
      >
        <select
          value={subjectType}
          onChange={(e) => setSubjectType(e.target.value as 'user' | 'role')}
          disabled={!canManage}
          aria-label="Grant to"
          className="px-2 py-1.5 rounded-lg bg-[#0F1826] border border-white/10 text-xs"
        >
          <option value="user">API key</option>
          <option value="role">Role</option>
        </select>
        <input
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          disabled={!canManage}
          placeholder={subjectType === 'role' ? 'Role name (e.g. operator)' : 'API key id'}
          aria-label="Subject"
          className="flex-1 min-w-[10rem] px-3 py-1.5 rounded-lg bg-[#0F1826] border border-white/10 text-xs"
        />
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value as WorkflowAccess)}
          disabled={!canManage}
          aria-label="Access level"
          className="px-2 py-1.5 rounded-lg bg-[#0F1826] border border-white/10 text-xs"
        >
          {ACCESS_LEVELS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
        </select>
        <button
          type="submit"
          disabled={!canManage || !subject.trim() || addMutation.isPending}
          className="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-xs font-medium disabled:opacity-50"
        >
          Grant
        </button>
      </form>
      {!canManage && (
        <p className="text-xs text-[#F1F5F9]/40 mt-2">Only workflow admins can change access.</p>
      )}
      {error && <p className="text-xs text-red-400 mt-2" role="alert">{error}</p>}
    </div>
  );
}

function SecretsPanel({ wf: _wf }: { wf: WEWorkflow }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-[#F1F5F9] mb-4">Secret References</h3>
      <p className="text-xs text-[#F1F5F9]/40 mb-4">
        Vault references used in this workflow. Values are never shown — only names are listed.
      </p>
      <div className="rounded-xl border border-white/10 p-4">
        <p className="text-xs text-[#F1F5F9]/30">
          Use <code className="text-sky-400">{'{{vault://SECRET_NAME}}'}</code> in step
          configuration to reference secrets stored in the vault.
        </p>
      </div>
    </div>
  );
}

function EnvVarsPanel({ wf }: { wf: WEWorkflow }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-[#F1F5F9] mb-4">Environment Variables</h3>
      <p className="text-xs text-[#F1F5F9]/40 mb-4">
        Key-value pairs accessible via <code className="text-sky-400">{'{{env.KEY}}'}</code> in steps.
      </p>
      <div className="rounded-xl border border-white/10 p-4">
        <p className="text-xs text-[#F1F5F9]/30">
          Workflow ID: {wf.id} | Version: {wf.version}
        </p>
      </div>
    </div>
  );
}

function NotificationsPanel({ wf: _wf }: { wf: WEWorkflow }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-[#F1F5F9] mb-4">Notification Rules</h3>
      <p className="text-xs text-[#F1F5F9]/40">
        Configure email, Slack, or webhook notifications for run events
        (run_completed, run_failed, hitl_requested, etc.).
      </p>
    </div>
  );
}

// Shows the workflow's REAL webhook (POST /wf-hooks/{signed-token}) fetched from
// the backend. It used to print /api/v1/webhooks/workflows/{id} — a route that
// does not exist — and claimed replay protection the endpoint does not have.
function WebhookPanel({ wf }: { wf: WEWorkflow }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['workflow-engine', 'webhook', wf.id],
    queryFn: () => workflowEngineApi.getWebhook(wf.id),
  });
  const url = data?.webhook_url
    ? data.webhook_url.startsWith('http')
      ? data.webhook_url
      : `${window.location.origin}${data.webhook_url}`
    : null;
  return (
    <div>
      <h3 className="text-sm font-semibold text-[#F1F5F9] mb-4">Webhook Trigger</h3>
      <div className="space-y-4">
        <div className="rounded-xl border border-white/10 bg-[#0F1826]/3 p-4">
          <p className="text-xs text-[#F1F5F9]/50 mb-2">Webhook URL</p>
          {isLoading ? (
            <p className="text-xs text-[#F1F5F9]/30">Loading…</p>
          ) : isError ? (
            <p className="text-xs text-rose-400">Could not load the webhook URL.</p>
          ) : url ? (
            <code className="text-xs text-sky-400 font-mono break-all">{url}</code>
          ) : (
            <p className="text-xs text-[#F1F5F9]/40">
              Publish this workflow to get its webhook URL.
            </p>
          )}
        </div>
        <p className="text-xs text-[#F1F5F9]/30">
          POST a JSON body to this URL to start a run; the body becomes the run inputs. The
          signed token in the URL is the credential — keep it secret. The workflow needs a
          webhook or api trigger. Run-completion callbacks are signed with HMAC-SHA256 in the{' '}
          <code>{data?.callback_signature_header ?? 'X-AgentVerse-Signature'}</code> header.
        </p>
        <WebhookDeliveries wf={wf} />
      </div>
    </div>
  );
}

const DELIVERY_STATUS_CLS: Record<string, string> = {
  succeeded: 'bg-emerald-500/15 text-emerald-400',
  failed: 'bg-amber-500/15 text-amber-400',
  pending: 'bg-sky-500/15 text-sky-400',
  dead: 'bg-red-500/15 text-red-400',
};

/** Deliveries whose run could not be started, and how their retries went. */
function WebhookDeliveries({ wf }: { wf: WEWorkflow }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['workflow-engine', 'webhook-events', wf.id],
    queryFn: () => workflowEngineApi.listWebhookEvents(wf.id, { per_page: 20 }),
  });
  const items = data?.items ?? [];
  return (
    <section aria-labelledby="webhook-deliveries-heading">
      <h4 id="webhook-deliveries-heading" className="text-xs font-semibold text-[#F1F5F9]/70 mb-2">
        Failed deliveries
      </h4>
      <p className="text-xs text-[#F1F5F9]/30 mb-2">
        When a delivery arrives but its run cannot be started it is kept and retried
        (up to 3 times, with backoff); after that it is marked dead.
      </p>
      <div className="rounded-xl border border-white/10 divide-y divide-white/5">
        {isLoading ? (
          <p className="p-3 text-xs text-[#F1F5F9]/30">Loading…</p>
        ) : isError ? (
          <p className="p-3 text-xs text-rose-400">Could not load deliveries.</p>
        ) : items.length === 0 ? (
          <p className="p-3 text-xs text-[#F1F5F9]/40">No failed deliveries.</p>
        ) : (
          items.map((d) => (
            <div key={d.id} className="p-3 text-xs space-y-1" data-testid="webhook-delivery">
              <div className="flex items-center gap-2">
                <span className={`px-2 py-0.5 rounded-full ${DELIVERY_STATUS_CLS[d.status] ?? 'bg-white/10'}`}>
                  {d.status}
                </span>
                <span className="text-[#F1F5F9]/50">
                  {d.attempts} {d.attempts === 1 ? 'retry' : 'retries'}
                </span>
                {d.received_at && (
                  <span className="ml-auto text-[#F1F5F9]/30">
                    {new Date(d.received_at).toLocaleString()}
                  </span>
                )}
              </div>
              {d.last_error && d.status !== 'succeeded' && (
                <p className="text-red-300/80 font-mono break-all">{d.last_error}</p>
              )}
              {d.run_id && <p className="text-[#F1F5F9]/40 font-mono">run {d.run_id}</p>}
            </div>
          ))
        )}
      </div>
    </section>
  );
}

const PANEL_COMPONENTS: Record<PanelId, React.FC<{ wf: WEWorkflow }>> = {
  general:       GeneralPanel,
  permissions:   PermissionsPanel,
  secrets:       SecretsPanel,
  env:           EnvVarsPanel,
  notifications: NotificationsPanel,
  webhook:       WebhookPanel,
};

// ── Main page ─────────────────────────────────────────────────────────────────

export default function WorkflowSettingsPage() {
  const { id } = useParams<{ id: string }>();
  const [activePanel, setActivePanel] = useState<PanelId>('general');

  const { data: wf, isLoading } = useQuery({
    queryKey: ['workflow-engine', 'get', id],
    queryFn: () => workflowEngineApi.get(id!),
    enabled: !!id,
  });

  if (isLoading || !wf) {
    return (
      <div className="min-h-screen bg-[#060810] flex items-center justify-center">
        <Loader2 className="h-6 w-6 text-sky-400 animate-spin" />
      </div>
    );
  }

  const PanelComponent = PANEL_COMPONENTS[activePanel];

  return (
    <JARVISPageShell>

      {/* Accessibility: announce loading state */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">{isLoading ? "Loading…" : ""}</div>
    <JARVISStagger className="min-h-screen bg-[#060810] text-[#F1F5F9]">
      <header className="sticky top-0 z-30 flex items-center gap-3 px-6 py-4 border-b
                          border-white/10 bg-[#060810]/90 backdrop-blur-xl">
        <Link to={`/workflows/${id}/edit`} className="text-[#F1F5F9]/40 hover:text-[#F1F5F9]"
          aria-label="Back to builder">
          <ChevronLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-sm font-bold">{wf.name} — Settings</h1>
          <p className="text-xs text-[#F1F5F9]/40">Workflow configuration</p>
        </div>
      </header>

      <div className="max-w-4xl mx-auto flex gap-8 px-6 py-8">
        {/* Sidebar nav */}
        <nav className="w-48 shrink-0" aria-label="Settings panels">
          <ul className="space-y-1">
            {PANELS.map(({ id: pid, label, icon: Icon }) => (
              <li key={pid}>
                <button
                  onClick={() => setActivePanel(pid)}
                  aria-current={activePanel === pid ? 'page' : undefined}
                  className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm
                    transition-colors ${
                      activePanel === pid
                        ? 'bg-sky-600/20 text-sky-400 font-medium'
                        : 'text-[#F1F5F9]/50 hover:text-[#F1F5F9] hover:bg-[#0A0D14]/5'
                    }`}
                >
                  <Icon className="h-4 w-4" aria-hidden />
                  {label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        {/* Panel content — animates when switching panels */}
        <main className="flex-1 min-w-0">
          <div
            key={activePanel}
            className="jarvis-rise-in"
          >
            <PanelComponent wf={wf} />
          </div>
        </main>
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
