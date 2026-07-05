import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useAuthStore } from '../../../stores/auth';

const API = import.meta.env.VITE_API_URL || '';
function apiFetch(path: string, apiKey: string, opts?: RequestInit) {
  return fetch(`${API}${path}`, { ...opts, headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json', ...opts?.headers } }).then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); });
}

interface VerifyResult {
  valid: boolean;
  records_checked: number;
  broken_at?: string;
}

export function AuditPanel() {
  const apiKey = useAuthStore(s => s.apiKey) || '';
  const [exportFormat, setExportFormat] = useState<'json' | 'csv'>('json');
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null);

  const verifyMutation = useMutation({
    mutationFn: () => apiFetch('/governance/audit/verify', apiKey, { method: 'POST' }),
    onSuccess: (data: VerifyResult) => setVerifyResult(data),
  });

  const exportMutation = useMutation({
    mutationFn: () => apiFetch(`/governance/audit/export?format=${exportFormat}`, apiKey),
    onSuccess: (data: { data: string }) => {
      const blob = new Blob([data.data], { type: exportFormat === 'csv' ? 'text/csv' : 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `audit-trail.${exportFormat}`;
      a.click();
    },
  });

  const { data: recentAudit } = useQuery({
    queryKey: ['audit-recent'],
    queryFn: () => apiFetch('/governance/audit/export?format=json', apiKey),
    enabled: !!apiKey,
  });

  let recentRecords: Record<string, unknown>[] = [];
  try {
    const raw = (recentAudit as { data?: string } | undefined)?.data;
    recentRecords = JSON.parse(raw || '[]').slice(0, 10) as Record<string, unknown>[];
  } catch { /* ignore parse errors */ }

  return (
    <div className="space-y-6">
      {/* Chain integrity */}
      <div className="rounded-xl border bg-card p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="font-semibold text-foreground">Hash Chain Integrity</h2>
            <p className="text-xs text-muted-foreground mt-0.5">Every record is cryptographically linked. Tampering is detectable.</p>
          </div>
          <button
            onClick={() => verifyMutation.mutate()}
            disabled={verifyMutation.isPending}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium disabled:opacity-50"
          >
            {verifyMutation.isPending ? 'Verifying…' : 'Verify Chain'}
          </button>
        </div>

        {verifyResult && (
          <div className={`rounded-lg p-4 ${verifyResult.valid ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800' : 'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800'}`}>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xl">{verifyResult.valid ? '✅' : '🚨'}</span>
              <p className={`font-semibold text-sm ${verifyResult.valid ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'}`}>
                {verifyResult.valid ? 'Chain Intact' : 'Chain Tampered!'}
              </p>
            </div>
            <p className="text-xs text-muted-foreground">
              {verifyResult.records_checked} records verified
              {!verifyResult.valid && ` · Broken at: ${verifyResult.broken_at}`}
            </p>
          </div>
        )}
      </div>

      {/* Export */}
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold text-foreground mb-3">Export Audit Trail</h2>
        <p className="text-xs text-muted-foreground mb-4">Download compliance evidence for SOC2, GDPR, HIPAA, or DPDP audits.</p>
        <div className="flex items-center gap-3">
          <select
            className="border rounded-lg px-3 py-2 bg-background text-foreground text-sm"
            value={exportFormat}
            onChange={e => setExportFormat(e.target.value as 'json' | 'csv')}
          >
            <option value="json">JSON (full detail)</option>
            <option value="csv">CSV (spreadsheet)</option>
          </select>
          <button
            onClick={() => exportMutation.mutate()}
            disabled={exportMutation.isPending}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm disabled:opacity-50"
          >
            {exportMutation.isPending ? 'Preparing…' : 'Download'}
          </button>
        </div>
      </div>

      {/* Recent records */}
      <div className="rounded-xl border bg-card p-5">
        <h2 className="font-semibold text-foreground mb-3">Recent Audit Records</h2>
        {recentRecords.length === 0 ? (
          <p className="text-sm text-muted-foreground">No audit records yet. Records appear when agents execute goals.</p>
        ) : (
          <div className="space-y-1">
            {recentRecords.map((r, i) => (
              <div key={String(r.id ?? i)} className="flex items-center gap-3 py-2 px-3 rounded hover:bg-muted/40 text-xs">
                <span className="text-muted-foreground w-16 flex-shrink-0">{String(r.sequence ?? '')}</span>
                <span className="font-mono text-muted-foreground w-28 flex-shrink-0">{String(r.entry_hash ?? '').slice(0, 12)}…</span>
                <span className="text-foreground flex-1">{String(r.action ?? '')}</span>
                <span className="text-muted-foreground">{String(r.tool_name ?? '')}</span>
                <span className="text-muted-foreground">{String(r.actor ?? '')}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
