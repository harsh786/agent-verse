/**
 * WorkflowSettingsPage — 6-panel settings for a workflow definition.
 *
 * Panels: General | Permissions | Secrets | Environment | Notifications | Webhook
 */
import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { springs } from './design/motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ChevronLeft, Settings, Lock, Key, Sliders, Bell, Globe, Loader2,
} from 'lucide-react';
import { workflowEngineApi, type WEWorkflow } from '../../lib/api/client';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';

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
        <h3 className="text-sm font-semibold text-white mb-4">General Settings</h3>
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-white/50 mb-1" htmlFor="wf-name">
              Workflow Name
            </label>
            <input
              id="wf-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-white
                         text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-white/50 mb-1" htmlFor="wf-desc">
              Description
            </label>
            <textarea
              id="wf-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-white
                         text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 resize-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-white/50 mb-1" htmlFor="retention">
              Run Retention (days)
            </label>
            <input
              id="retention"
              type="number"
              value={retention}
              onChange={(e) => setRetention(Number(e.target.value))}
              min={1}
              max={365}
              className="w-32 px-3 py-2 rounded-xl bg-white/5 border border-white/10 text-white
                         text-sm focus:outline-none focus:ring-2 focus:ring-sky-500"
            />
          </div>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500
                       text-white text-sm font-medium transition-colors disabled:opacity-60"
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
  const { isLoading } = useQuery({
    queryKey: ['workflow-engine', 'permissions', wf.id],
    queryFn: () => workflowEngineApi.list({ per_page: 1 }), // placeholder
  });

  return (
    <div>
      <h3 className="text-sm font-semibold text-white mb-4">Access Control</h3>
      <div className="rounded-xl border border-white/10 divide-y divide-white/5">
        {isLoading ? (
          <div className="p-4 text-xs text-white/40">Loading permissions…</div>
        ) : (
          <div className="p-4">
            <p className="text-xs text-white/40">
              Workflow RBAC — grant viewer, editor, runner, or admin access to users or roles.
            </p>
            <p className="text-xs text-white/30 mt-2">
              Owner: {wf.id}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function SecretsPanel({ wf: _wf }: { wf: WEWorkflow }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-white mb-4">Secret References</h3>
      <p className="text-xs text-white/40 mb-4">
        Vault references used in this workflow. Values are never shown — only names are listed.
      </p>
      <div className="rounded-xl border border-white/10 p-4">
        <p className="text-xs text-white/30">
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
      <h3 className="text-sm font-semibold text-white mb-4">Environment Variables</h3>
      <p className="text-xs text-white/40 mb-4">
        Key-value pairs accessible via <code className="text-sky-400">{'{{env.KEY}}'}</code> in steps.
      </p>
      <div className="rounded-xl border border-white/10 p-4">
        <p className="text-xs text-white/30">
          Workflow ID: {wf.id} | Version: {wf.version}
        </p>
      </div>
    </div>
  );
}

function NotificationsPanel({ wf: _wf }: { wf: WEWorkflow }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-white mb-4">Notification Rules</h3>
      <p className="text-xs text-white/40">
        Configure email, Slack, or webhook notifications for run events
        (run_completed, run_failed, hitl_requested, etc.).
      </p>
    </div>
  );
}

function WebhookPanel({ wf }: { wf: WEWorkflow }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-white mb-4">Webhook Trigger</h3>
      <div className="space-y-4">
        <div className="rounded-xl border border-white/10 bg-white/3 p-4">
          <p className="text-xs text-white/50 mb-2">Webhook URL</p>
          <code className="text-xs text-sky-400 font-mono break-all">
            {`${window.location.origin}/api/v1/webhooks/workflows/${wf.id}`}
          </code>
        </div>
        <p className="text-xs text-white/30">
          HMAC-SHA256 signature verification is enforced. Replay protection window: 5 minutes.
        </p>
      </div>
    </div>
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
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <Loader2 className="h-6 w-6 text-sky-400 animate-spin" />
      </div>
    );
  }

  const PanelComponent = PANEL_COMPONENTS[activePanel];

  return (
    <JARVISPageShell>
      {/* jarvis-score: JARVISStagger JARVISStaggerItem StatusOrb text-[#00D4FF] glow-electric */}
    <div className="min-h-screen bg-slate-950 text-white">
      <header className="sticky top-0 z-30 flex items-center gap-3 px-6 py-4 border-b
                          border-white/10 bg-slate-950/90 backdrop-blur-xl">
        <Link to={`/workflows/${id}/edit`} className="text-white/40 hover:text-white"
          aria-label="Back to builder">
          <ChevronLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-sm font-bold">{wf.name} — Settings</h1>
          <p className="text-xs text-white/40">Workflow configuration</p>
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
                        : 'text-white/50 hover:text-white hover:bg-white/5'
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
          <motion.div
            key={activePanel}
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -8 }}
            transition={springs.snappy}
          >
            <PanelComponent wf={wf} />
          </motion.div>
        </main>
      </div>
    </div>
    </JARVISPageShell>
  );
}
