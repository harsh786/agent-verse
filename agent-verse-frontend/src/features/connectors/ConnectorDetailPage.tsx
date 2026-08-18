import { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Activity, ArrowLeft, CheckCircle, Loader2, Pencil, Wrench, XCircle, Zap } from 'lucide-react';
import { connectorsApi } from '@/lib/api/client';
import { DetailLayout } from '@/components/detail/DetailLayout';
import { Skeleton } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { toast } from '@/stores/toast';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'health', label: 'Health' },
  { key: 'usage', label: 'Usage' },
];

// ── HealthTab ─────────────────────────────────────────────────────────────────

function HealthTab({ connectorId, connector }: { connectorId: string; connector: any }) {
  const qc = useQueryClient();
  const [liveResult, setLiveResult] = useState<import('@/lib/api/client').ConnectorTestResult | null>(null);

  const testMutation = useMutation({
    mutationFn: () => connectorsApi.test(connectorId),
    onSuccess: (data) => {
      setLiveResult(data);
      qc.invalidateQueries({ queryKey: ['connector', connectorId] });
      if (data.reachable) {
        toast({ kind: 'success', message: data.detail ? `Connected: ${data.detail}` : 'Connection test passed!' });
      } else {
        toast({ kind: 'error', message: data.error ?? 'Connection test failed' });
      }
    },
    onError: (e) => toast({ kind: 'error', message: `Test failed: ${String(e)}` }),
  });

  const lastTested = connector?.last_tested;
  // Use live result from this session first; fall back to connector.test_result
  const testResult = liveResult ?? connector?.test_result;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-medium">Connection Status</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {lastTested ? `Last tested ${new Date(lastTested).toLocaleString()}` : 'Never tested'}
          </p>
        </div>
        <button
          onClick={() => testMutation.mutate()}
          disabled={testMutation.isPending}
          className="flex items-center gap-2 px-3 py-1.5 bg-[#00D4FF] text-[#00D4FF]-foreground text-sm rounded-lg hover:opacity-90 disabled:opacity-50"
        >
          {testMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
          {testMutation.isPending ? 'Testing…' : 'Test Connection'}
        </button>
      </div>

      {/* Live test result */}
      {testResult && (
        <div className={`p-4 rounded-lg border ${
          (testResult.reachable ?? testResult.success)
            ? 'bg-green-50 border-green-200 dark:bg-green-900/20 dark:border-green-800'
            : 'bg-red-50 border-red-200 dark:bg-red-900/20 dark:border-red-800'
        }`}>
          <div className="flex items-center gap-2 mb-2">
            {(testResult.reachable ?? testResult.success)
              ? <CheckCircle className="h-4 w-4 text-green-600" />
              : <XCircle className="h-4 w-4 text-red-600" />}
            <span className="text-sm font-medium">
              {(testResult.reachable ?? testResult.success) ? 'Connection successful' : 'Connection failed'}
            </span>
            {testResult.latency_ms != null && (
              <span className="ml-auto text-xs text-muted-foreground">{testResult.latency_ms} ms</span>
            )}
          </div>
          {/* Detail line — e.g. "Authenticated as @username · scopes: repo,read:org" */}
          {testResult.detail && (
            <p className="text-xs text-green-700 dark:text-green-400 mb-1 font-medium">{testResult.detail}</p>
          )}
          {testResult.mcp_url && (
            <p className="text-xs text-muted-foreground">
              MCP endpoint: <code className="font-mono">{testResult.mcp_url}</code>
            </p>
          )}
          {testResult.error && (
            <p className="text-xs text-red-600 dark:text-red-400 mt-1 whitespace-pre-line">{testResult.error}</p>
          )}
        </div>
      )}

      <div className="bg-muted/30 rounded-lg p-4">
        <p className="text-xs text-muted-foreground">
          Run a connection test to verify your credentials and endpoint are valid.
          Tests are non-destructive and do not modify any data.
        </p>
      </div>
    </div>
  );
}

// ── UsageTab ──────────────────────────────────────────────────────────────────

function UsageTab({ connectorId }: { connectorId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['connector-usage', connectorId],
    queryFn: () => connectorsApi.getUsage(connectorId),
    staleTime: 60_000,
  });

  const goals = data?.goals ?? [];
  const total = data?.total ?? 0;
  const successRate = data?.success_rate;
  const isFiltered = data?.filtered ?? false;

  return (
    <div className="space-y-4">
      {/* Header note */}
      <p className="text-xs text-muted-foreground">
        {isFiltered
          ? `Goals that referenced this connector`
          : 'Recent goals using this connector'}
      </p>

      {/* Stats cards */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Total Goals</p>
          <p className="text-2xl font-bold mt-1">{total.toLocaleString()}</p>
        </div>
        <div className="bg-card border border-border rounded-lg p-4">
          <p className="text-xs text-muted-foreground">Success Rate</p>
          <p className={`text-2xl font-bold mt-1 ${
            successRate != null ? (successRate >= 80 ? 'text-green-600' : successRate >= 60 ? 'text-amber-600' : 'text-red-600') : 'text-muted-foreground'
          }`}>
            {successRate != null ? `${successRate}%` : '—'}
          </p>
        </div>
      </div>

      {/* Goals list */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-14 rounded-lg bg-muted animate-pulse" />
          ))}
        </div>
      ) : goals.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-28 text-muted-foreground">
          <Activity className="h-7 w-7 opacity-20 mb-2" />
          <p className="text-sm">No goals found for this connector</p>
        </div>
      ) : (
        <div className="space-y-2">
          {goals.map((g: any) => (
            <Link
              key={g.id}
              to={`/goals/${g.id}`}
              className="flex items-center justify-between p-3 bg-muted/30 rounded-lg hover:bg-muted/50 transition-colors"
            >
              <p className="text-sm truncate max-w-xs">{g.goal}</p>
              <span className={`text-xs px-2 py-0.5 rounded-full shrink-0 ml-2 ${
                g.status === 'complete' ? 'bg-green-100 text-green-800' :
                g.status === 'failed' ? 'bg-red-100 text-red-800' :
                'bg-yellow-100 text-yellow-800'
              }`}>{g.status}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// ── ConnectorDetailPage ───────────────────────────────────────────────────────

export function ConnectorDetailPage() {
  const { connectorId } = useParams<{ connectorId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState('overview');
  const [testResult, setTestResult] = useState<string | null>(null);

  // FIX 1: Use API client instead of raw fetch
  const { data: connector, isLoading } = useQuery({
    queryKey: ['connector', connectorId],
    queryFn: () => connectorsApi.get(connectorId!),
    enabled: !!connectorId,
  });

  const { data: tools = [], isLoading: toolsLoading } = useQuery({
    queryKey: ['connector-tools', connectorId],
    queryFn: () => connectorsApi.tools(connectorId!),
    enabled: !!connectorId,
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
        <button onClick={() => navigate('/connectors')} className="text-[#00D4FF] hover:underline">
          Back
        </button>
      </div>
    );
  }

  return (
    <JARVISPageShell>
    <JARVISStagger className="space-y-0">
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
            {/* FIX 4: Edit Credentials button */}
            <button
              onClick={() =>
                navigate('/connectors', { state: { editConnectorId: connectorId } })
              }
              className="flex items-center gap-2 px-3 py-1.5 border border-input text-sm rounded-lg hover:bg-muted/50"
            >
              <Pencil className="h-4 w-4" />
              Edit Credentials
            </button>
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
              <div
                className={`p-3 rounded-lg text-sm ${
                  testResult.startsWith('✓')
                    ? 'bg-green-50 text-green-800'
                    : 'bg-red-50 text-red-800'
                }`}
              >
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
                <EmptyState
          icon={<Wrench size={40} />}
          title="No tools discovered"
          description="Run discovery to see available tools."
          variant="float"
        />
              ) : (
                <ul className="space-y-1">
                  {tools.map((t, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-2 text-sm py-1 border-b last:border-0"
                    >
                      <span className="font-mono text-xs bg-muted px-1.5 py-0.5 rounded">
                        {t.name ?? `tool_${i}`}
                      </span>
                      <span className="text-muted-foreground text-xs">{t.description ?? ''}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}

        {/* FIX 2: Health tab — real implementation */}
        {activeTab === 'health' && (
          <HealthTab connectorId={connectorId!} connector={connector} />
        )}

        {/* FIX 3: Usage tab — real implementation */}
        {activeTab === 'usage' && <UsageTab connectorId={connectorId!} />}
      </DetailLayout>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
