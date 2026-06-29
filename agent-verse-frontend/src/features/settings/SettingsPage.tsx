import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Eye, EyeOff, RefreshCw, Trash2, Copy, Check } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

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
  model: string;
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

// ── Helpers ───────────────────────────────────────────────────────────────────

const hdrs = (apiKey: string) => ({
  'X-API-Key': apiKey,
  'Content-Type': 'application/json',
});

async function apiFetch<T>(apiKey: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: hdrs(apiKey), ...init });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Profile section ───────────────────────────────────────────────────────────

function ProfileSection({ apiKey }: { apiKey: string }) {
  const { data: tenant, isLoading, error } = useQuery({
    queryKey: ['tenant-me'],
    queryFn: () => apiFetch<Tenant>(apiKey, '/tenants/me'),
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
  const [editing, setEditing] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [form, setForm] = useState<LLMConfig>({ provider: 'openai', model: 'gpt-4o', api_key: '', base_url: '' });

  const { data: llmConfig, isLoading, error } = useQuery({
    queryKey: ['llm-config'],
    queryFn: () => apiFetch<LLMConfig>(apiKey, '/tenants/me/llm'),
    enabled: !!apiKey,
  });

  useEffect(() => {
    if (llmConfig) setForm({ ...llmConfig, api_key: llmConfig.api_key ?? '', base_url: llmConfig.base_url ?? '' });
  }, [llmConfig]);

  const saveMutation = useMutation({
    mutationFn: () =>
      apiFetch<LLMConfig>(apiKey, '/tenants/me/llm', {
        method: 'PUT',
        body: JSON.stringify(form),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['llm-config'] });
      setEditing(false);
    },
  });

  if (isLoading) return <SectionShell title="LLM Provider"><p className="text-sm text-muted-foreground">Loading…</p></SectionShell>;
  if (error) return <SectionShell title="LLM Provider"><p className="text-sm text-red-500">Failed to load LLM config.</p></SectionShell>;

  return (
    <SectionShell
      title="LLM Provider"
      action={
        <button onClick={() => setEditing((v) => !v)} className="text-sm text-primary hover:opacity-70">
          {editing ? 'Cancel' : 'Edit'}
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
                  model: MODEL_SUGGESTIONS[p]?.[0] ?? '',
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
              value={form.model}
              onChange={(e) => setForm((f) => ({ ...f, model: e.target.value }))}
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
              disabled={!form.provider || !form.model || saveMutation.isPending}
              className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
            >
              {saveMutation.isPending ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      ) : (
        <dl className="space-y-3">
          {[
            { label: 'Provider', value: llmConfig?.provider ?? '—' },
            { label: 'Model', value: llmConfig?.model ?? '—', mono: true },
            { label: 'API Key', value: llmConfig?.api_key ? '••••••••' : 'Not set' },
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

  const { data: keys = [], isLoading, error } = useQuery({
    queryKey: ['api-keys'],
    queryFn: () => apiFetch<ApiKey[]>(apiKey, '/tenants/me/keys'),
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<CreatedKey>(apiKey, '/tenants/me/keys', {
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
      apiFetch<CreatedKey>(apiKey, `/tenants/me/keys/${id}/rotate`, { method: 'POST' }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['api-keys'] });
      setNewlyCreated(data);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<void>(apiKey, `/tenants/me/keys/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['api-keys'] }),
  });

  const copyToClipboard = async (text: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
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
                      onClick={() => deleteMutation.mutate(k.key_id)}
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

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Profile, LLM provider, and API key management
        </p>
      </div>
      <ProfileSection apiKey={apiKey} />
      <LLMProviderSection apiKey={apiKey} />
      <ApiKeysSection apiKey={apiKey} />
    </div>
  );
}
