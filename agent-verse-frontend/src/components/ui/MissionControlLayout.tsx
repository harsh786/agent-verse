/**
 * Mission Control Layout
 * Cockpit-style shell: operational status bar at top,
 * sidebar left, center live canvas, optional right inspector drawer.
 */
import { ReactNode, useState, useEffect } from 'react';
import { Activity, AlertTriangle, Clock } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';
import { useNavigate } from 'react-router-dom';

interface MissionControlLayoutProps {
  children: ReactNode;
  rightPanel?: ReactNode;
  showOperationalBar?: boolean;
}

function OperationalStatusBar() {
  const navigate = useNavigate();
  const [now, setNow] = useState(() => new Date().toLocaleTimeString());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date().toLocaleTimeString()), 1000);
    return () => clearInterval(id);
  }, []);

  const { data: health } = useQuery({
    queryKey: ['mission-health'],
    queryFn: () => apiFetch<{ status?: string }>('/health').catch(() => ({ status: 'unknown' })),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });

  const { data: goals } = useQuery({
    queryKey: ['mission-active-goals'],
    queryFn: () =>
      apiFetch<{ goals?: Array<{ status: string }> }>('/goals').catch(() => ({ goals: [] })),
    refetchInterval: 5_000,
    staleTime: 3_000,
  });

  const { data: alerts } = useQuery({
    queryKey: ['mission-alerts'],
    queryFn: () =>
      apiFetch<{ alerts?: Array<{ severity: string }> }>('/ai-ops/alerts?limit=3').catch(
        () => ({ alerts: [] })
      ),
    refetchInterval: 30_000,
  });

  const { data: regression } = useQuery({
    queryKey: ['mission-regression'],
    queryFn: () =>
      apiFetch<{ status?: string }>('/ai-ops/regression-status').catch(() => ({ status: 'ok' })),
    refetchInterval: 60_000,
  });

  const activeGoals = (goals?.goals ?? []).filter((g) =>
    ['executing', 'planning'].includes(g.status)
  );
  const criticalAlerts = (alerts?.alerts ?? []).filter(
    (a) => a.severity === 'critical'
  ).length;
  const systemStatus =
    health?.status === 'healthy' || health?.status === 'ok' ? 'operational' : 'degraded';
  const regressionStatus = regression?.status ?? 'ok';

  return (
    <div className="h-10 bg-command-black border-b border-neural-violet/20 flex items-center px-4 gap-6 shrink-0 z-50">
      {/* System Status */}
      <div className="flex items-center gap-1.5 text-xs">
        {systemStatus === 'operational' ? (
          <div className="w-1.5 h-1.5 rounded-full bg-verified-green animate-pulse" />
        ) : (
          <div className="w-1.5 h-1.5 rounded-full bg-risk-amber animate-pulse" />
        )}
        <span
          className={
            systemStatus === 'operational' ? 'text-verified-green' : 'text-risk-amber'
          }
        >
          {systemStatus === 'operational' ? 'Operational' : 'Degraded'}
        </span>
      </div>

      <div className="w-px h-4 bg-white/10" />

      {/* Active Goals */}
      <button
        onClick={() => navigate('/goals?status=executing')}
        className="flex items-center gap-1.5 text-xs text-white/60 hover:text-white transition-colors"
      >
        <Activity className="h-3 w-3 text-telemetry-cyan" />
        <span className="font-mono text-telemetry-cyan">{activeGoals.length}</span>
        <span>active</span>
      </button>

      {/* Alerts */}
      {criticalAlerts > 0 && (
        <>
          <div className="w-px h-4 bg-white/10" />
          <button
            onClick={() => navigate('/ai-ops')}
            className="flex items-center gap-1.5 text-xs text-mission-red hover:opacity-80"
          >
            <AlertTriangle className="h-3 w-3" />
            <span className="font-mono">{criticalAlerts}</span>
            <span>critical</span>
          </button>
        </>
      )}

      {/* Regression */}
      {regressionStatus !== 'ok' && (
        <>
          <div className="w-px h-4 bg-white/10" />
          <div className="flex items-center gap-1.5 text-xs text-risk-amber">
            <AlertTriangle className="h-3 w-3" />
            <span>Regression {regressionStatus}</span>
          </div>
        </>
      )}

      <div className="flex-1" />

      {/* Time */}
      <div className="flex items-center gap-1.5 text-xs text-white/40 font-mono">
        <Clock className="h-3 w-3" />
        {now}
      </div>
    </div>
  );
}

export function MissionControlLayout({
  children,
  rightPanel,
  showOperationalBar = true,
}: MissionControlLayoutProps) {
  return (
    <div className="flex flex-col h-full">
      {showOperationalBar && <OperationalStatusBar />}
      <div className="flex flex-1 overflow-hidden">
        <main className="flex-1 overflow-auto p-6">{children}</main>
        {rightPanel && (
          <aside className="w-80 border-l border-neural-violet/20 bg-panel-graphite/50 overflow-auto p-4 shrink-0">
            {rightPanel}
          </aside>
        )}
      </div>
    </div>
  );
}
