import React, { useState, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Eye, EyeOff, RefreshCw, Trash2, Copy, Check,
  User, Cpu, KeyRound, Shield, Bell, Palette, AlertTriangle,
  Sun, Moon, Monitor, CheckCircle, Loader2, Download, CreditCard,
} from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { useThemeStore } from '@/stores/theme';
import { toast } from '@/stores/toast';
import { apiFetch as apiClient, tenantsApi } from '@/lib/api/client';
import { MFASettings } from './MFASettings';

import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';
// ── Types ─────────────────────────────────────────────────────────────────────

interface Tenant {
  tenant_id: string;
  name: string;
  email?: string;
  plan: string;
  created_at?: string;
}

interface LLMConfig {
  provider: string;
  /** Canonical field name matching the backend (was `model` — Gap 1 fix) */
  default_model: string;
  api_key?: string;
  base_url?: string;
}

interface ApiKey {
  key_id: string;
  name: string;
  created_at: string;
  last_used_at?: string;
}

interface CreatedKey extends ApiKey {
  raw_key: string;
}

// ── Settings tabs ─────────────────────────────────────────────────────────────

const SETTINGS_TABS = [
  { id: 'profile',       label: 'General',       icon: User          },
  { id: 'llm',           label: 'LLM Providers', icon: Cpu           },
  { id: 'apikeys',       label: 'API Keys',      icon: KeyRound      },
  { id: 'security',      label: 'Security',      icon: Shield        },
  { id: 'notifications', label: 'Notifications', icon: Bell          },
  { id: 'appearance',    label: 'Appearance',    icon: Palette       },
  { id: 'danger',        label: 'Danger Zone',   icon: AlertTriangle },
] as const;

type TabId = typeof SETTINGS_TABS[number]['id'];

// ── Confirm modal ─────────────────────────────────────────────────────────────

function ConfirmModal({
  isOpen,
  title,
  description,
  onConfirm,
  onCancel,
  confirmLabel = 'Confirm',
  isDestructive = false,
}: {
  isOpen: boolean;
  title: string;
  description: string;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
  isDestructive?: boolean;
}) {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-card border border-border rounded-xl p-6 max-w-sm w-full mx-4 shadow-xl">
        <h3 className="font-semibold text-base">{title}</h3>
        <p className="text-sm text-muted-foreground mt-2">{description}</p>
        <div className="flex gap-3 mt-6 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm border border-border rounded-lg hover:bg-[#1A1F2E] hover:shadow-glow-electric transition-[background-color,box-shadow]"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className={`px-4 py-2 text-sm rounded-lg transition-colors ${
              isDestructive
                ? 'bg-red-600 text-white hover:bg-red-700'
                : 'bg-primary text-primary-foreground hover:opacity-90'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Profile section ───────────────────────────────────────────────────────────

function ProfileSection({ apiKey }: { apiKey: string }) {
  const { data: tenant, isLoading, error } = useQuery({
    queryKey: ['tenant-me'],
    queryFn: () => apiClient<Tenant>('/tenants/me'),
    enabled: !!apiKey,
  });

  if (isLoading) return <SectionShell title="Profile"><p className="text-sm text-muted-foreground">Loading…</p></SectionShell>;
  if (error) return <SectionShell title="Profile"><p className="text-sm text-red-500">Failed to load tenant info.</p></SectionShell>;

  return (
    <SectionShell title="Profile">
      <dl className="space-y-3">
        {[
          { label: 'Tenant ID', value: tenant?.tenant_id ?? '—', mono: true },
          { label: 'Name', value: tenant?.name ?? '—' },
          { label: 'Email', value: tenant?.email ?? '—' },
          { label: 'Plan', value: tenant?.plan ?? '—' },
          {
            label: 'Created',
            value: tenant?.created_at
              ? new Date(tenant.created_at).toLocaleDateString()
              : '—',
          },
        ].map(({ label, value, mono }) => (
          <div key={label} className="flex justify-between text-sm">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className={`font-medium ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
          </div>
        ))}
      </dl>
    </SectionShell>
  );
}

// ── LLM Provider section ──────────────────────────────────────────────────────

const LLM_PROVIDERS = ['anthropic', 'openai', 'groq', 'ollama', 'gemini', 'azure_openai'];
const MODEL_SUGGESTIONS: Record<string, string[]> = {
  anthropic: ['claude-opus-4-5', 'claude-sonnet-4-5', 'claude-haiku-3-5'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
  gemini: ['gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-2.0-flash'],
  azure_openai: ['gpt-4o', 'gpt-4-turbo'],
  groq: ['llama-3.1-70b-versatile', 'llama-3.1-8b-instant', 'mixtral-8x7b-32768'],
  ollama: ['llama3.2', 'llama3.1', 'mistral', 'qwen2.5'],
};

function LLMProviderSection({ apiKey }: { apiKey: string }) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [form, setForm] = useState<LLMConfig>({ provider: 'openai', default_model: 'gpt-4o', api_key: '', base_url: '' });

  // Gap 1 fix: use tenantsApi.getLLMConfig (/tenants/me/llm-config) which is the
  // canonical lightweight config endpoint; falls back to the encrypted /me/llm endpoint
  // for reading the displayed provider/model (merged below).
  const { data: llmConfig, isLoading: llmConfigLoading } = useQuery({
    queryKey: ['llm-config-simple'],
    queryFn: () => tenantsApi.getLLMConfig(),
    enabled: !!apiKey,
  });

  const { data: llmFull, isLoading: llmFullLoading } = useQuery({
    queryKey: ['llm-config'],
    queryFn: () => apiClient<LLMConfig>('/tenants/me/llm'),
    enabled: !!apiKey,
  });

  const isLoading = llmConfigLoading || llmFullLoading;

  useEffect(() => {
    // Prefer the full encrypted endpoint for display (has provider/default_model/masked_key)
    if (llmFull) {
      setForm({
        provider: (llmFull as any).provider ?? 'openai',
        default_model: (llmFull as any).default_model ?? (llmConfig as any)?.default_model ?? (llmConfig as any)?.model ?? 'gpt-4o',
        api_key: '',
        base_url: (llmFull as any).base_url ?? '',
      });
    } else if (llmConfig) {
      setForm({
        provider: (llmConfig as any).provider ?? 'openai',
        default_model: (llmConfig as any).default_model ?? (llmConfig as any).model ?? 'gpt-4o',
        api_key: '',
        base_url: (llmConfig as any).base_url ?? '',
      });
    }
  }, [llmFull, llmConfig]);

  const saveMutation = useMutation({
    mutationFn: () =>
      // Gap 1 fix: send `default_model` to the full encrypted endpoint, and also
      // persist to the lightweight /me/llm-config endpoint for cross-service reads.
      Promise.all([
        apiClient<LLMConfig>('/tenants/me/llm', {
          method: 'PUT',
          body: JSON.stringify({
            provider: form.provider,
            api_key: form.api_key || undefined,
            base_url: form.base_url || undefined,
            default_model: form.default_model,
          }),
        }),
        tenantsApi.saveLLMConfig({
          provider: form.provider,
          default_model: form.default_model,
          base_url: form.base_url || undefined,
        }),
      ]),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['llm-config'] });
      qc.invalidateQueries({ queryKey: ['llm-config-simple'] });
      setEditing(false);
    },
  });

  if (isLoading) return <SectionShell title="LLM Provider"><p className="text-sm text-muted-foreground">Loading…</p></SectionShell>;

  return (
    <SectionShell
      title="LLM Provider"
      action={
        <button onClick={() => setEditing((v) => !v)} className="text-sm text-primary hover:opacity-70">
          {editing ? t('common.cancel') : t('common.edit')}
        </button>
      }
    >
      {editing ? (
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium mb-1">Provider</label>
            <select
              value={form.provider}
              onChange={(e) => {
                const p = e.target.value;
                setForm((f) => ({
                  ...f,
                  provider: p,
                  default_model: MODEL_SUGGESTIONS[p]?.[0] ?? '',
                }));
              }}
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            >
              {LLM_PROVIDERS.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">Model</label>
            <input
              value={form.default_model}
              onChange={(e) => setForm((f) => ({ ...f, default_model: e.target.value }))}
              list="model-suggestions"
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            />
            <datalist id="model-suggestions">
              {(MODEL_SUGGESTIONS[form.provider] ?? []).map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">API Key</label>
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                value={form.api_key}
                onChange={(e) => setForm((f) => ({ ...f, api_key: e.target.value }))}
                placeholder="sk-..."
                className="w-full border border-input rounded-lg px-3 py-2 pr-10 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium mb-1">
              Base URL{' '}
              <span className="text-muted-foreground font-normal">(optional — for Ollama, Groq, or custom endpoints)</span>
            </label>
            <input
              type="url"
              value={form.base_url ?? ''}
              onChange={(e) => setForm((f) => ({ ...f, base_url: e.target.value }))}
              placeholder="https://api.groq.com/openai/v1"
              className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
            />
          </div>
          {saveMutation.isError && (
            <p className="text-xs text-red-600">{String(saveMutation.error)}</p>
          )}
          <div className="flex justify-end">
            <button
              onClick={() => saveMutation.mutate()}
              disabled={!form.provider || !form.default_model || saveMutation.isPending}
              className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
            >
              {saveMutation.isPending ? 'Saving…' : t('common.save')}
            </button>
          </div>
        </div>
      ) : (
        <dl className="space-y-3">
          {[
            { label: 'Provider', value: (llmFull as any)?.provider ?? (llmConfig as any)?.provider ?? '—' },
            { label: 'Model', value: (llmFull as any)?.default_model ?? (llmConfig as any)?.default_model ?? (llmConfig as any)?.model ?? '—', mono: true },
            { label: 'API Key', value: (llmFull as any)?.masked_key ? '••••••••' : 'Not set' },
          ].map(({ label, value, mono }) => (
            <div key={label} className="flex justify-between text-sm">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className={`font-medium ${mono ? 'font-mono text-xs' : ''}`}>{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </SectionShell>
  );
}

// ── API Keys section ──────────────────────────────────────────────────────────

function ApiKeysSection({ apiKey }: { apiKey: string }) {
  const qc = useQueryClient();
  const [newKeyName, setNewKeyName] = useState('');
  const [showCreateInput, setShowCreateInput] = useState(false);
  const [newlyCreated, setNewlyCreated] = useState<CreatedKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [deleteKeyId, setDeleteKeyId] = useState<string | null>(null);

  const { data: keys = [], isLoading, error } = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => apiClient<ApiKey[]>('/tenants/me/keys'),
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      apiClient<CreatedKey>('/tenants/me/keys', {
        method: 'POST',
        body: JSON.stringify({ name: newKeyName }),
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      setNewlyCreated(data);
      setNewKeyName('');
      setShowCreateInput(false);
    },
  });

  const rotateMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient<CreatedKey>(`/tenants/me/keys/${id}/rotate`, { method: 'POST' }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      setNewlyCreated(data);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient<void>(`/tenants/me/keys/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['api-keys'] }),
  });

  const copyToClipboard = async (text: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const pendingDeleteKey = keys.find((k) => k.key_id === deleteKeyId);

  return (
    <>
      <SectionShell
        title="API Keys"
        action={
          <button
            onClick={() => setShowCreateInput((v) => !v)}
            className="text-sm text-primary hover:opacity-70"
          >
            {showCreateInput ? 'Cancel' : '+ New Key'}
          </button>
        }
      >
        {showCreateInput && (
          <div className="flex gap-2 mb-4">
            <input
              value={newKeyName}
              onChange={(e) => setNewKeyName(e.target.value)}
              placeholder="Key name (e.g. production)"
              className="flex-1 border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
              onKeyDown={(e) => e.key === 'Enter' && createMutation.mutate()}
            />
            <button
              onClick={() => createMutation.mutate()}
              disabled={!newKeyName.trim() || createMutation.isPending}
              className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating…' : 'Create'}
            </button>
          </div>
        )}

        {/* Newly created key banner */}
        {newlyCreated && (
          <div className="mb-4 p-3 bg-green-50 dark:bg-green-950/30 border border-green-200 dark:border-green-800 rounded-lg">
            <p className="text-xs font-medium text-green-800 dark:text-green-300 mb-1">
              Key created — copy it now, it won't be shown again
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs font-mono bg-card border border-green-300 dark:border-green-700 rounded px-2 py-1 overflow-auto">
                {newlyCreated.raw_key}
              </code>
              <button
                onClick={() => copyToClipboard(newlyCreated.raw_key)}
                className="p-1.5 hover:bg-green-100 rounded transition-colors"
              >
                {copied ? (
                  <Check className="h-4 w-4 text-green-700" />
                ) : (
                  <Copy className="h-4 w-4 text-green-700" />
                )}
              </button>
            </div>
            <button
              onClick={() => setNewlyCreated(null)}
              className="text-xs text-green-700 mt-2 hover:underline"
            >
              Dismiss
            </button>
          </div>
        )}

        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : error ? (
          <p className="text-sm text-red-500">Failed to load API keys.</p>
        ) : keys.length === 0 ? (
          <p className="text-sm text-muted-foreground">No API keys. Create one above.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Name', 'Created', 'Last Used', 'Actions'].map((h) => (
                  <th key={h} className="text-left py-2 font-medium text-muted-foreground text-xs">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {keys.map((k) => (
                <tr key={k.key_id}>
                  <td className="py-2.5 font-medium">{k.name}</td>
                  <td className="py-2.5 text-muted-foreground text-xs">
                    {new Date(k.created_at).toLocaleDateString()}
                  </td>
                  <td className="py-2.5 text-muted-foreground text-xs">
                    {k.last_used_at ? new Date(k.last_used_at).toLocaleDateString() : 'Never'}
                  </td>
                  <td className="py-2.5">
                    <div className="flex gap-3">
                      <button
                        onClick={() => rotateMutation.mutate(k.key_id)}
                        disabled={rotateMutation.isPending}
                        title="Rotate"
                        className="text-primary hover:opacity-70 disabled:opacity-40 p-0.5"
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                      </button>
                      <button
                        onClick={() => setDeleteKeyId(k.key_id)}
                        disabled={deleteMutation.isPending}
                        title="Delete"
                        className="text-destructive hover:opacity-70 disabled:opacity-40 p-0.5"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </SectionShell>

      {/* Confirm delete modal */}
      <ConfirmModal
        isOpen={!!deleteKeyId}
        title="Delete API Key"
        description={`Are you sure you want to delete "${pendingDeleteKey?.name ?? 'this key'}"? Any services using it will immediately lose access.`}
        onConfirm={() => {
          if (deleteKeyId) {
            deleteMutation.mutate(deleteKeyId);
            setDeleteKeyId(null);
          }
        }}
        onCancel={() => setDeleteKeyId(null)}
        confirmLabel="Delete Key"
        isDestructive
      />
    </>
  );
}

// ── Security tab ──────────────────────────────────────────────────────────────

function SecurityTab() {
  const { apiKey } = useAuthStore();
  const qc = useQueryClient();

  const { data: sessions = [] } = useQuery({
    queryKey: ['tenant-sessions'],
    queryFn: () => apiClient<{ session_id?: string; id?: string; device?: string; last_seen?: string }[]>('/tenants/me/sessions').catch(() => []),
    staleTime: 60_000,
    enabled: !!apiKey,
  });

  const revokeSessionMutation = useMutation({
    mutationFn: (sessionId: string) =>
      apiClient(`/tenants/me/sessions/${sessionId}`, { method: 'DELETE' }),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Session revoked' });
      qc.invalidateQueries({ queryKey: ['tenant-sessions'] });
    },
    onError: () => toast({ kind: 'error', message: 'Failed to revoke session' }),
  });

  return (
    <div className="space-y-6 max-w-lg">
      <div>
        <h3 className="text-base font-semibold">Active Sessions</h3>
        <p className="text-sm text-muted-foreground mt-1">Manage where you're signed in to AgentVerse.</p>
        <div className="mt-4 space-y-2">
          {/* Always show current session */}
          <div className="flex items-center justify-between p-4 border border-border rounded-xl">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-green-100 dark:bg-green-900/30 rounded-lg">
                <Monitor className="h-4 w-4 text-green-700 dark:text-green-400" />
              </div>
              <div>
                <p className="text-sm font-medium">Current session</p>
                <p className="text-xs text-muted-foreground">
                  {navigator.userAgent.includes('Mac') ? 'macOS' : 'Web'} · API Key authentication · Active now
                </p>
              </div>
            </div>
            <span className="text-xs bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 px-2 py-0.5 rounded-full">
              This device
            </span>
          </div>
          {sessions.slice(0, 5).map((s, i) => (
            <div key={i} className="flex items-center justify-between p-4 border border-border rounded-xl">
              <div>
                <p className="text-sm font-medium">{s.device ?? 'Unknown device'}</p>
                <p className="text-xs text-muted-foreground">Last seen {s.last_seen ?? 'recently'}</p>
              </div>
              <button
                onClick={() => revokeSessionMutation.mutate(s.session_id ?? s.id ?? '')}
                disabled={revokeSessionMutation.isPending}
                className="text-xs text-destructive hover:underline disabled:opacity-50"
              >
                Revoke
              </button>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="text-base font-semibold">Two-Factor Authentication</h3>
        <MFASettings />
      </div>

      <div>
        <h3 className="text-base font-semibold">API Key Management</h3>
        <p className="text-sm text-muted-foreground mt-1">
          Rotate your API key if you believe it has been compromised.{' '}
          <Link to="/settings/scopes" className="text-primary hover:underline">
            Manage scopes →
          </Link>
        </p>
      </div>
    </div>
  );
}

// ── Notifications tab ─────────────────────────────────────────────────────────

const NOTIF_KEY = 'av_notification_prefs';

type NotifPrefs = {
  goalComplete: boolean;
  goalFailed: boolean;
  budgetAlert: boolean;
  hitlPending: boolean;
  weeklyReport: boolean;
};

const DEFAULT_NOTIF_PREFS: NotifPrefs = {
  goalComplete: true,
  goalFailed: true,
  budgetAlert: true,
  hitlPending: true,
  weeklyReport: false,
};

function NotificationsTab() {
  const [prefs, setPrefs] = useState<NotifPrefs>(() => {
    try {
      const stored = localStorage.getItem(NOTIF_KEY);
      return stored ? (JSON.parse(stored) as NotifPrefs) : DEFAULT_NOTIF_PREFS;
    } catch {
      return DEFAULT_NOTIF_PREFS;
    }
  });
  const [saving, setSaving] = useState(false);

  const toggle = async (key: keyof NotifPrefs) => {
    const next = { ...prefs, [key]: !prefs[key] };
    setPrefs(next);
    localStorage.setItem(NOTIF_KEY, JSON.stringify(next));
    setSaving(true);
    try {
      await apiClient('/tenants/me/notifications', {
        method: 'PUT',
        body: JSON.stringify(next),
      });
    } catch {
      // Backend may not have this endpoint yet — localStorage fallback is enough
    } finally {
      setSaving(false);
    }
  };

  const NOTIF_ITEMS: { key: keyof NotifPrefs; label: string; desc: string; icon: string }[] = [
    { key: 'goalComplete', label: 'Goal completed',    desc: 'When a goal finishes successfully',      icon: '✅' },
    { key: 'goalFailed',   label: 'Goal failed',       desc: 'When a goal encounters an error',        icon: '❌' },
    { key: 'budgetAlert',  label: 'Budget alerts',     desc: 'When spending approaches your limits',   icon: '💰' },
    { key: 'hitlPending',  label: 'Approval requests', desc: 'When a goal needs human approval',       icon: '⏳' },
    { key: 'weeklyReport', label: 'Weekly digest',     desc: 'Summary of agent activity every Monday', icon: '📊' },
  ];

  return (
    <div className="space-y-6 max-w-lg">
      <div>
        <h3 className="text-base font-semibold">Notification Preferences</h3>
        <p className="text-sm text-muted-foreground mt-1">
          Choose which events trigger notifications.
          {saving && <span className="ml-2 text-primary">Saving…</span>}
        </p>
      </div>
      <div className="space-y-3">
        {NOTIF_ITEMS.map(({ key, label, desc, icon }) => (
          <div
            key={key}
            onClick={() => toggle(key)}
            className="flex items-center justify-between p-4 border border-border rounded-xl cursor-pointer hover:bg-muted/30 transition-colors"
          >
            <div className="flex items-center gap-3">
              <span className="text-xl">{icon}</span>
              <div>
                <p className="text-sm font-medium">{label}</p>
                <p className="text-xs text-muted-foreground">{desc}</p>
              </div>
            </div>
            <div
              role="switch"
              aria-checked={prefs[key]}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                prefs[key] ? 'bg-primary' : 'bg-muted'
              }`}
            >
              <span className={`inline-block h-4 w-4 rounded-full bg-[#0F1826] shadow transition-transform ${
                prefs[key] ? 'translate-x-6' : 'translate-x-1'
              }`} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Appearance tab ────────────────────────────────────────────────────────────

function AppearanceTab() {
  const { theme, density, setTheme, setDensity } = useThemeStore();

  return (
    <div className="space-y-8 max-w-lg">
      {/* Theme */}
      <div>
        <h3 className="text-base font-semibold mb-1">Theme</h3>
        <p className="text-sm text-muted-foreground mb-4">Choose how AgentVerse looks to you.</p>
        <div className="grid grid-cols-3 gap-3">
          {(['light', 'dark', 'system'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTheme(t)}
              className={`relative p-4 border-2 rounded-xl text-sm font-medium transition-[color,background-color,border-color,opacity,box-shadow,transform] capitalize ${
                theme === t
                  ? 'border-primary bg-primary/5 text-primary'
                  : 'border-border hover:border-primary/40'
              }`}
            >
              <div className={`w-full h-12 rounded-lg mb-2 flex items-center justify-center ${
                t === 'light' ? 'bg-[#0F1826] border border-white/[0.08]' :
                t === 'dark' ? 'bg-gray-900' :
                'bg-gradient-to-r from-white to-gray-900'
              }`}>
                {t === 'light' ? <Sun className="h-5 w-5 text-yellow-500" /> :
                 t === 'dark' ? <Moon className="h-5 w-5 text-blue-400" /> :
                 <Monitor className="h-5 w-5 text-muted-foreground" />}
              </div>
              {t}
              {theme === t && (
                <CheckCircle className="absolute top-2 right-2 h-4 w-4 text-primary" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Density */}
      <div>
        <h3 className="text-base font-semibold mb-1">Information Density</h3>
        <p className="text-sm text-muted-foreground mb-4">Control how much information is shown at once.</p>
        <div className="grid grid-cols-3 gap-3">
          {([
            { value: 'compact',     label: 'Compact',     desc: 'More content, less spacing' },
            { value: 'default',     label: 'Default',     desc: 'Balanced spacing'           },
            { value: 'comfortable', label: 'Comfortable', desc: 'More whitespace'             },
          ] as const).map(({ value, label, desc }) => (
            <button
              key={value}
              onClick={() => setDensity(value)}
              className={`p-4 border-2 rounded-xl text-left transition-[color,background-color,border-color,opacity,box-shadow,transform] ${
                density === value
                  ? 'border-primary bg-primary/5'
                  : 'border-border hover:border-primary/40'
              }`}
            >
              <p className="text-sm font-medium">{label}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{desc}</p>
              {density === value && (
                <CheckCircle className="h-3.5 w-3.5 text-primary mt-2" />
              )}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Danger Zone tab ───────────────────────────────────────────────────────────

function DeleteConfirmInput({
  onConfirm,
  onCancel,
  isLoading,
}: {
  onConfirm: () => void;
  onCancel: () => void;
  isLoading: boolean;
}) {
  const [text, setText] = useState('');
  return (
    <div className="flex gap-2">
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Type DELETE"
        className="flex-1 px-3 py-2 text-sm border border-red-300 rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-red-400"
      />
      <button
        onClick={onConfirm}
        disabled={text !== 'DELETE' || isLoading}
        className="px-3 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50"
      >
        {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Confirm'}
      </button>
      <button
        onClick={onCancel}
        className="px-3 py-2 text-sm border border-input rounded-lg hover:bg-muted/50"
      >
        Cancel
      </button>
    </div>
  );
}

function DangerZoneTab() {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { logout } = useAuthStore();

  const exportMutation = useMutation({
    mutationFn: () => apiClient<Blob>('/tenants/me/export', { method: 'POST' }),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(
        blob instanceof Blob ? blob : new Blob([JSON.stringify(blob)])
      );
      const a = document.createElement('a');
      a.href = url;
      a.download = `agentverse-export-${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast({ kind: 'success', message: 'Data exported successfully' });
    },
    onError: () => toast({ kind: 'error', message: 'Export failed. Try again.' }),
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiClient('/tenants/me', { method: 'DELETE' }),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Account deleted. You will be signed out.' });
      setTimeout(() => logout(), 2000);
    },
    onError: () => toast({ kind: 'error', message: 'Account deletion failed.' }),
  });

  return (
    <div className="space-y-6 max-w-lg">
      <div className="p-5 border border-orange-200 dark:border-orange-800 rounded-xl bg-orange-50/50 dark:bg-orange-900/10">
        <h3 className="text-base font-semibold text-orange-700 dark:text-orange-400">Export All Data</h3>
        <p className="text-sm text-muted-foreground mt-1 mb-4">
          Download a complete copy of all your goals, agents, templates, configurations, and audit logs as JSON.
        </p>
        <button
          onClick={() => exportMutation.mutate()}
          disabled={exportMutation.isPending}
          className="flex items-center gap-2 px-4 py-2 text-sm border border-orange-300 dark:border-orange-700 rounded-lg text-orange-700 dark:text-orange-400 hover:bg-orange-100 dark:hover:bg-orange-900/30 disabled:opacity-50 transition-colors"
        >
          {exportMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Download className="h-4 w-4" />
          )}
          {exportMutation.isPending ? 'Exporting…' : 'Export Data'}
        </button>
      </div>

      <div className="p-5 border border-red-200 dark:border-red-800 rounded-xl bg-red-50/50 dark:bg-red-900/10">
        <h3 className="text-base font-semibold text-red-700 dark:text-red-400">Delete Account</h3>
        <p className="text-sm text-muted-foreground mt-1 mb-4">
          Permanently delete your tenant account, all agents, goals, and data. This cannot be undone.
        </p>
        {!confirmDelete ? (
          <button
            onClick={() => setConfirmDelete(true)}
            className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
          >
            Delete Account
          </button>
        ) : (
          <div className="space-y-3">
            <p className="text-sm font-medium text-red-700">Are you absolutely sure? Type DELETE to confirm:</p>
            <DeleteConfirmInput
              onConfirm={() => deleteMutation.mutate()}
              onCancel={() => setConfirmDelete(false)}
              isLoading={deleteMutation.isPending}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Shared shell ──────────────────────────────────────────────────────────────

function SectionShell({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex justify-between items-center mb-4">
        <h2 className="font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function SettingsPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = (searchParams.get('tab') as TabId) ?? 'profile';
  const setActiveTab = (tab: string) => setSearchParams({ tab });

  const renderTab = () => {
    switch (activeTab) {
      case 'profile':
        return <ProfileSection apiKey={apiKey} />;
      case 'llm':
        return <LLMProviderSection apiKey={apiKey} />;
      case 'apikeys':
        return <ApiKeysSection apiKey={apiKey} />;
      case 'security':
        return <SecurityTab />;
      case 'notifications':
        return <NotificationsTab />;
      case 'appearance':
        return <AppearanceTab />;
      case 'danger':
        return <DangerZoneTab />;
      default:
        return null;
    }
  };

  return (
    <JARVISPageShell>

      {/* Accessibility: announce loading state */}
      <div aria-live="polite" aria-atomic="true" className="sr-only"></div>
    <JARVISStagger className="max-w-4xl">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">{t('settings.title')}</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Manage your profile, providers, keys, and preferences
        </p>
      </div>

      <div className="flex gap-6">
        {/* Left sub-nav */}
        <aside className="w-48 flex-shrink-0">
          <nav className="space-y-0.5" aria-label="Settings sections">
            {SETTINGS_TABS.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg transition-colors text-left ${
                  activeTab === id
                    ? 'bg-primary/10 text-primary font-medium'
                    : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'
                }`}
              >
                <Icon className="h-4 w-4 flex-shrink-0" />
                <span className="truncate">{label}</span>
              </button>
            ))}
            {/* Billing — separate route, not a tab */}
            <Link
              to="/settings/billing"
              className="w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg transition-colors text-left text-muted-foreground hover:bg-muted/60 hover:text-foreground"
            >
              <CreditCard className="h-4 w-4 flex-shrink-0" />
              <span className="truncate">Billing</span>
            </Link>
          </nav>
        </aside>

        {/* Right content pane */}
        <div className="flex-1 min-w-0">
          {renderTab()}
        </div>
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
