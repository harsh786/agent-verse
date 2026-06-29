import { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';

const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

interface Connector {
  server_id: string;
  name: string;
  url: string;
  auth_type: string;
  auth_config?: Record<string, string>;
  description?: string;
  priority?: number;
  status?: string;
}

interface RegisterForm {
  name: string;
  url: string;
  auth_type: string;
  auth_config: string;
}

interface TestResult {
  reachable: boolean;
  status: string;
  latency_ms?: number;
}

async function fetchConnectors(apiKey: string): Promise<Connector[]> {
  const res = await fetch(`${API_BASE}/connectors`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`Failed to fetch connectors: ${res.statusText}`);
  return res.json();
}

async function registerConnector(apiKey: string, data: Omit<RegisterForm, 'auth_config'> & { auth_config: Record<string, string> }): Promise<Connector> {
  const res = await fetch(`${API_BASE}/connectors`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to register connector: ${res.statusText}`);
  return res.json();
}

async function updateConnector(
  apiKey: string,
  id: string,
  data: Omit<RegisterForm, 'auth_config'> & { auth_config: Record<string, string> }
): Promise<Connector> {
  const res = await fetch(`${API_BASE}/connectors/${id}`, {
    method: 'PUT',
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Failed to update connector: ${res.statusText}`);
  return res.json();
}

async function unregisterConnector(apiKey: string, id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/connectors/${id}`, {
    method: 'DELETE',
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`Failed to remove connector: ${res.statusText}`);
}

async function testConnector(apiKey: string, id: string): Promise<TestResult> {
  const res = await fetch(`${API_BASE}/connectors/${id}/test`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`Failed to test connector: ${res.statusText}`);
  return res.json();
}

const AUTH_TYPES = [
  'bearer', 'api_key', 'basic', 'oauth_ac', 'pkce',
  'oauth_cc', 'custom_header', 'mtls', 'hmac',
];

const INITIAL_FORM: RegisterForm = {
  name: '',
  url: '',
  auth_type: 'bearer',
  auth_config: '{}',
};

export function ConnectorsRegisteredPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const qc = useQueryClient();
  const location = useLocation();
  const prefill = (location.state as any)?.prefill;

  const [showRegister, setShowRegister] = useState(!!prefill);
  const [form, setForm] = useState<RegisterForm>({
    name: prefill?.name ?? '',
    url: prefill?.url ?? prefill?.default_url ?? '',
    auth_type: prefill?.auth_type ?? 'bearer',
    auth_config: '{}',
  });
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formError, setFormError] = useState('');
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  const { data: connectors = [], isLoading, error } = useQuery({
    queryKey: ['connectors'],
    queryFn: () => fetchConnectors(apiKey),
    enabled: !!apiKey,
  });

  const registerMutation = useMutation({
    mutationFn: () => {
      let parsed: Record<string, string> = {};
      try {
        parsed = JSON.parse(form.auth_config || '{}');
      } catch {
        throw new Error('auth_config must be valid JSON');
      }
      if (editingId) {
        return updateConnector(apiKey, editingId, { ...form, auth_config: parsed });
      }
      return registerConnector(apiKey, { ...form, auth_config: parsed });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['connectors'] });
      setShowRegister(false);
      setEditingId(null);
      setForm(INITIAL_FORM);
      setFormError('');
    },
    onError: (e) => setFormError(String(e)),
  });

  const unregisterMutation = useMutation({
    mutationFn: (id: string) => unregisterConnector(apiKey, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['connectors'] }),
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => testConnector(apiKey, id),
    onSuccess: (data, id) =>
      setTestResults((prev) => ({ ...prev, [id]: data })),
  });

  function openCreateModal() {
    setEditingId(null);
    setForm(INITIAL_FORM);
    setFormError('');
    setShowRegister(true);
  }

  function openEditModal(connector: Connector) {
    setEditingId(connector.server_id);
    setForm({
      name: connector.name,
      url: connector.url,
      auth_type: connector.auth_type,
      auth_config: JSON.stringify(connector.auth_config ?? {}, null, 2),
    });
    setFormError('');
    setShowRegister(true);
  }

  function closeModal() {
    setShowRegister(false);
    setEditingId(null);
    setForm(INITIAL_FORM);
    setFormError('');
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Registered Connectors</h1>
          <p className="text-muted-foreground text-sm mt-1">
            MCP servers registered for your tenant
          </p>
        </div>
        <button
          onClick={openCreateModal}
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg hover:opacity-90 text-sm font-medium"
        >
          + Register Connector
        </button>
      </div>

      {/* Table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            Loading connectors…
          </div>
        ) : error ? (
          <div className="px-5 py-10 text-center text-sm text-red-500">
            Failed to load connectors.
          </div>
        ) : connectors.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            No connectors registered yet. Register one to get started.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                {['Name', 'URL', 'Auth Type', 'Status', 'Actions'].map((h) => (
                  <th
                    key={h}
                    className="text-left px-4 py-3 font-medium text-muted-foreground"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {connectors.map((c) => {
                const result = testResults[c.server_id];
                return (
                  <tr key={c.server_id} className="hover:bg-accent/50 transition-colors">
                    <td className="px-4 py-3 font-medium">{c.name}</td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground max-w-xs truncate">
                      {c.url}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{c.auth_type}</td>
                    <td className="px-4 py-3">
                      {result ? (
                        <span
                          className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                            result.reachable
                              ? 'bg-green-100 text-green-800'
                              : 'bg-red-100 text-red-800'
                          }`}
                        >
                          {result.status}
                        </span>
                      ) : (
                        <span className="text-muted-foreground text-xs">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-3">
                        <button
                          onClick={() => testMutation.mutate(c.server_id)}
                          disabled={testMutation.isPending}
                          className="text-primary hover:opacity-70 text-sm disabled:opacity-40 transition-opacity"
                        >
                          Test
                        </button>
                        <button
                          onClick={() => openEditModal(c)}
                          className="text-primary hover:opacity-70 text-sm transition-opacity"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => unregisterMutation.mutate(c.server_id)}
                          disabled={unregisterMutation.isPending}
                          className="text-destructive hover:opacity-70 text-sm disabled:opacity-40 transition-opacity"
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Register modal */}
      {showRegister && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-card border border-border rounded-xl p-6 w-full max-w-md shadow-2xl">
            <h2 className="text-xl font-semibold mb-4">
              {editingId ? 'Edit MCP Connector' : 'Register MCP Connector'}
            </h2>
            <div className="space-y-3">
              {(
                [
                  { label: 'Name', key: 'name', placeholder: 'my-github' },
                  { label: 'URL', key: 'url', placeholder: 'http://localhost:9000' },
                ] as const
              ).map(({ label, key, placeholder }) => (
                <div key={key}>
                  <label className="block text-sm font-medium mb-1">{label}</label>
                  <input
                    value={form[key]}
                    onChange={(e) =>
                      setForm((f) => ({ ...f, [key]: e.target.value }))
                    }
                    placeholder={placeholder}
                    className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
                  />
                </div>
              ))}
              <div>
                <label className="block text-sm font-medium mb-1">Auth Type</label>
                <select
                  value={form.auth_type}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, auth_type: e.target.value }))
                  }
                  className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
                >
                  {AUTH_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">
                  Auth Config (JSON)
                </label>
                <textarea
                  value={form.auth_config}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, auth_config: e.target.value }))
                  }
                  placeholder='{"token": "..."}'
                  rows={3}
                  className="w-full border border-input rounded-lg px-3 py-2 text-sm font-mono bg-background focus:ring-2 focus:ring-primary outline-none resize-none"
                />
              </div>
            </div>
            {formError && (
              <p role="alert" className="text-xs text-red-600 mt-2">
                {formError}
              </p>
            )}
            <div className="flex gap-3 mt-4 justify-end">
              <button
                onClick={closeModal}
                className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => registerMutation.mutate()}
                disabled={!form.name.trim() || !form.url.trim() || registerMutation.isPending}
                className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {registerMutation.isPending
                  ? editingId ? 'Saving…' : 'Registering…'
                  : editingId ? 'Save Changes' : 'Register'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
