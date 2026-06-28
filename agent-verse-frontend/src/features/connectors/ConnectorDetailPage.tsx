import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Loader2 } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { connectorsApi } from '@/lib/api/client';
import { DetailLayout } from '@/components/detail/DetailLayout';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { toast } from '@/stores/toast';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'health', label: 'Health' },
  { key: 'usage', label: 'Usage' },
];

export function ConnectorDetailPage() {
  const { connectorId } = useParams<{ connectorId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const apiKey = useAuthStore((s) => s.apiKey);
  const [activeTab, setActiveTab] = useState('overview');
  const [testResult, setTestResult] = useState<string | null>(null);

  const { data: connector, isLoading } = useQuery({
    queryKey: ['connector', connectorId],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/connectors/${connectorId}`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    },
    enabled: !!connectorId && !!apiKey,
  });

  // Discover tools
  const { data: tools = [], isLoading: toolsLoading } = useQuery({
    queryKey: ['connector-tools', connectorId],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/connectors/${connectorId}/tools`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (!res.ok) return [];
      return res.json();
    },
    enabled: !!connectorId && !!apiKey && activeTab === 'overview',
  });

  const testMutation = useMutation({
    mutationFn: () => connectorsApi.test(connectorId!),
    onSuccess: (data) => {
      const msg = data.reachable
        ? `✓ Reachable (${data.latency_ms ?? '?'}ms)`
        : `✗ Unreachable: ${data.error ?? 'unknown error'}`;
      setTestResult(msg);
      toast({ kind: data.reachable ? 'success' : 'error', message: msg });
    },
    onError: (e) => toast({ kind: 'error', message: `Test failed: ${e}` }),
  });

  const deleteMutation = useMutation({
    mutationFn: () => connectorsApi.unregister(connectorId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['connectors'] });
      navigate('/connectors');
      toast({ kind: 'success', message: 'Connector removed.' });
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-40">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!connector) {
    return (
      <div className="text-center py-20 text-muted-foreground">
        Connector not found.{' '}
        <button onClick={() => navigate('/connectors')} className="text-primary hover:underline">
          Back
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-0">
      <div className="px-6 py-3">
        <button
          onClick={() => navigate('/connectors')}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> Back to Connectors
        </button>
      </div>

      <DetailLayout
        title={connector.name ?? connectorId}
        subtitle={connector.url}
        status={connector.status ?? 'unknown'}
        meta={[
          { label: 'Auth type', value: connector.auth_type ?? '—' },
          { label: 'Server ID', value: connector.server_id ?? '—' },
        ]}
        actions={
          <>
            <button
              onClick={() => testMutation.mutate()}
              disabled={testMutation.isPending}
              aria-label="Test connector connection"
              className="px-3 py-1.5 text-sm border rounded-md hover:bg-muted disabled:opacity-50"
            >
              {testMutation.isPending ? 'Testing…' : 'Test Connection'}
            </button>
            <button
              onClick={() => {
                if (confirm('Remove this connector?')) deleteMutation.mutate();
              }}
              aria-label="Remove connector"
              className="px-3 py-1.5 text-sm border border-destructive text-destructive rounded-md hover:bg-destructive/10"
            >
              Remove
            </button>
          </>
        }
        tabs={TABS}
        activeTab={activeTab}
        onTabChange={setActiveTab}
      >
        {activeTab === 'overview' && (
          <div className="space-y-4">
            {testResult && (
              <div className={`p-3 rounded-lg text-sm ${testResult.startsWith('✓') ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-800'}`}>
                {testResult}
              </div>
            )}
            <div className="bg-card border rounded-xl p-4">
              <h3 className="font-medium text-sm mb-3">Connector Info</h3>
              <dl className="grid grid-cols-2 gap-y-2 text-sm">
                {[
                  ['URL', connector.url],
                  ['Auth type', connector.auth_type ?? '—'],
                  ['Status', connector.status ?? '—'],
                ].map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd className="font-medium truncate">{v}</dd>
                  </div>
                ))}
              </dl>
            </div>

            <div className="bg-card border rounded-xl p-4">
              <h3 className="font-medium text-sm mb-3">Exposed Tools</h3>
              {toolsLoading ? (
                <Skeleton className="h-16 w-full" />
              ) : tools.length === 0 ? (
                <EmptyState title="No tools discovered" description="Run discovery to see available tools." />
              ) : (
                <ul className="space-y-1">
                  {tools.map((t: { name?: string; description?: string }, i: number) => (
                    <li key={i} className="flex items-start gap-2 text-sm py-1 border-b last:border-0">
                      <span className="font-mono text-xs bg-muted px-1.5 py-0.5 rounded">{t.name ?? `tool_${i}`}</span>
                      <span className="text-muted-foreground text-xs">{t.description ?? ''}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}

        {activeTab === 'health' && (
          <EmptyState
            title="Health history"
            description="Connection health checks will appear here once the connector is polled."
          />
        )}

        {activeTab === 'usage' && (
          <EmptyState
            title="Usage"
            description="Goals and agents using this connector will appear here."
          />
        )}
      </DetailLayout>
    </div>
  );
}
