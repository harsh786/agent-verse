import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Download, Trash2, Globe, AlertTriangle } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

interface ResidencyInfo {
  region: string;
  data_center?: string;
  compliance_frameworks?: string[];
  description?: string;
}

interface ExportResult {
  download_url?: string;
  expires_at?: string;
  size_bytes?: number;
  message?: string;
}

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

// ── Export section ────────────────────────────────────────────────────────────

function ExportSection({ apiKey }: { apiKey: string }) {
  const [result, setResult] = useState<ExportResult | null>(null);

  const mutation = useMutation({
    mutationFn: () => apiFetch<ExportResult>(apiKey, '/enterprise/compliance/export'),
    onSuccess: (data) => setResult(data),
  });

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h2 className="font-semibold flex items-center gap-2">
            <Download className="h-4 w-4" /> Export My Data
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Download a copy of all your data including goals, agents, and audit logs.
          </p>
        </div>
        <button
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
          className="flex items-center gap-1.5 bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50 ml-4 flex-shrink-0"
        >
          <Download className="h-3.5 w-3.5" />
          {mutation.isPending ? 'Exporting…' : 'Export'}
        </button>
      </div>

      {mutation.isError && (
        <p className="text-sm text-red-500 mt-2">{String(mutation.error)}</p>
      )}

      {result && (
        <div className="mt-3 p-3 bg-green-50 border border-green-200 rounded-lg text-sm">
          {result.download_url ? (
            <div className="space-y-1.5">
              <p className="text-green-800 font-medium">Export ready</p>
              {result.size_bytes != null && (
                <p className="text-green-700 text-xs">
                  Size: {(result.size_bytes / 1024 / 1024).toFixed(2)} MB
                </p>
              )}
              {result.expires_at && (
                <p className="text-green-700 text-xs">
                  Expires: {new Date(result.expires_at).toLocaleString()}
                </p>
              )}
              <a
                href={result.download_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block text-green-800 underline text-xs"
              >
                Download export →
              </a>
            </div>
          ) : (
            <p className="text-green-800">{result.message ?? 'Export completed.'}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Delete section ────────────────────────────────────────────────────────────

function DeleteSection({ apiKey }: { apiKey: string }) {
  const [showConfirm, setShowConfirm] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [deleted, setDeleted] = useState(false);

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<void>(apiKey, '/enterprise/compliance/delete', { method: 'POST' }),
    onSuccess: () => {
      setDeleted(true);
      setShowConfirm(false);
    },
  });

  if (deleted) {
    return (
      <div className="bg-card border border-border rounded-xl p-5">
        <div className="flex items-center gap-2 text-green-600">
          <Trash2 className="h-4 w-4" />
          <p className="font-medium text-sm">Data deletion scheduled.</p>
        </div>
        <p className="text-sm text-muted-foreground mt-1">
          Your data will be permanently deleted within 30 days.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-card border border-red-200 rounded-xl p-5">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <h2 className="font-semibold flex items-center gap-2 text-red-700">
            <Trash2 className="h-4 w-4" /> Delete My Data
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Permanently delete all your data. This action cannot be undone.
          </p>
        </div>
        {!showConfirm && (
          <button
            onClick={() => setShowConfirm(true)}
            className="flex items-center gap-1.5 bg-red-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-red-700 ml-4 flex-shrink-0"
          >
            <AlertTriangle className="h-3.5 w-3.5" />
            Delete
          </button>
        )}
      </div>

      {showConfirm && (
        <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-lg space-y-3">
          <p className="text-sm font-medium text-red-800">
            Are you absolutely sure? Type{' '}
            <span className="font-mono font-bold">DELETE MY DATA</span> to confirm.
          </p>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="DELETE MY DATA"
            className="w-full border border-red-300 rounded-lg px-3 py-2 text-sm bg-white outline-none focus:ring-2 focus:ring-red-400"
          />
          {mutation.isError && (
            <p className="text-xs text-red-600">{String(mutation.error)}</p>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => {
                setShowConfirm(false);
                setConfirmText('');
              }}
              className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent"
            >
              Cancel
            </button>
            <button
              onClick={() => mutation.mutate()}
              disabled={confirmText !== 'DELETE MY DATA' || mutation.isPending}
              className="bg-red-600 text-white px-4 py-2 rounded-lg text-sm hover:bg-red-700 disabled:opacity-50"
            >
              {mutation.isPending ? 'Deleting…' : 'Confirm Delete'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Residency section ─────────────────────────────────────────────────────────

function ResidencySection({ apiKey }: { apiKey: string }) {
  const { data: residency, isLoading, error } = useQuery({
    queryKey: ['residency'],
    queryFn: () => apiFetch<ResidencyInfo>(apiKey, '/enterprise/compliance/residency'),
    enabled: !!apiKey,
  });

  return (
    <div className="bg-card border border-border rounded-xl p-5">
      <h2 className="font-semibold flex items-center gap-2 mb-3">
        <Globe className="h-4 w-4" /> Data Residency
      </h2>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : error ? (
        <p className="text-sm text-red-500">Failed to load residency info.</p>
      ) : residency ? (
        <dl className="space-y-3">
          {[
            { label: 'Region', value: residency.region },
            { label: 'Data Center', value: residency.data_center ?? '—' },
          ].map(({ label, value }) => (
            <div key={label} className="flex justify-between text-sm">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="font-medium">{value}</dd>
            </div>
          ))}
          {residency.compliance_frameworks && residency.compliance_frameworks.length > 0 && (
            <div className="flex justify-between text-sm">
              <dt className="text-muted-foreground">Compliance</dt>
              <dd className="flex gap-1.5 flex-wrap justify-end">
                {residency.compliance_frameworks.map((f) => (
                  <span
                    key={f}
                    className="bg-blue-100 text-blue-800 text-xs px-2 py-0.5 rounded-full font-medium"
                  >
                    {f}
                  </span>
                ))}
              </dd>
            </div>
          )}
          {residency.description && (
            <p className="text-sm text-muted-foreground pt-1 border-t border-border">
              {residency.description}
            </p>
          )}
        </dl>
      ) : (
        <p className="text-sm text-muted-foreground">No residency info available.</p>
      )}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function EnterprisePage() {
  const apiKey = useAuthStore((s) => s.apiKey);

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h1 className="text-2xl font-bold">Enterprise</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Compliance, data management, and residency controls
        </p>
      </div>
      <ExportSection apiKey={apiKey} />
      <ResidencySection apiKey={apiKey} />
      <DeleteSection apiKey={apiKey} />
    </div>
  );
}
