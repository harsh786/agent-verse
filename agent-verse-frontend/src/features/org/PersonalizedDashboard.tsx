/**
 * PersonalizedDashboard — role-aware home screen.
 *
 * Routes to the correct role-specific dashboard based on user role.
 * Falls back to a universal default view.
 *
 * JARVIS motion system:
 *   - JARVISPageShell:  blur-in page entry
 *   - AnimatePresence:  role switch transition
 */
import { lazy, Suspense } from 'react';
import { } from 'lucide-react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import type { Organization } from './types';

// ── Lazy role dashboards ───────────────────────────────────────────────────

const CEODashboard    = lazy(() => import('./dashboards/CEODashboard').then(m => ({ default: m.CEODashboard })));
const CTODashboard    = lazy(() => import('./dashboards/CTODashboard').then(m => ({ default: m.CTODashboard })));
const CMODashboard    = lazy(() => import('./dashboards/CMODashboard').then(m => ({ default: m.CMODashboard })));
const CFODashboard    = lazy(() => import('./dashboards/CFODashboard').then(m => ({ default: m.CFODashboard })));
const HRODashboard    = lazy(() => import('./dashboards/HRODashboard').then(m => ({ default: m.HRODashboard })));
const SalesDashboard  = lazy(() => import('./dashboards/SalesDashboard').then(m => ({ default: m.SalesDashboard })));
const DevOpsDashboard = lazy(() => import('./dashboards/DevOpsDashboard').then(m => ({ default: m.DevOpsDashboard })));

// ── Role map ───────────────────────────────────────────────────────────────

const ROLE_COMPONENT: Record<string, React.ComponentType<{ org: Organization }>> = {
  ceo:       CEODashboard,
  cto:       CTODashboard,
  cmo:       CMODashboard,
  cfo:       CFODashboard,
  chro:      HRODashboard,
  hr:        HRODashboard,
  sales:     SalesDashboard,
  devops:    DevOpsDashboard,
  sre:       DevOpsDashboard,
};

// ── Loading fallback ───────────────────────────────────────────────────────

function DashboardSkeleton() {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 animate-pulse">
      {[1, 2, 3, 4, 5, 6, 7, 8].map(i => (
        <div key={i} className="h-24 rounded-xl bg-[#0F1623]" aria-hidden />
      ))}
    </div>
  );
}

// ── Default dashboard (shown when no role matched) ─────────────────────────

function DefaultDashboard({ org }: { org: Organization }) {
  return (
    <div className="space-y-4">
      <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-[#F1F5F9] mb-1">{org.name}</h2>
        <p className="text-xs text-[#475569]">
          {org.description || 'AI Organization OS — use the sidebar to navigate to departments, missions, and more.'}
        </p>
      </div>
    </div>
  );
}

// ── Main ───────────────────────────────────────────────────────────────────

interface PersonalizedDashboardProps {
  org: Organization;
  /** User's role — determines which dashboard to show */
  userRole?: string;
}

export function PersonalizedDashboard({ org, userRole }: PersonalizedDashboardProps) {
  const roleKey     = userRole?.toLowerCase();
  const DashboardComponent = roleKey ? ROLE_COMPONENT[roleKey] : null;

  return (
    <JARVISPageShell>
      <Suspense fallback={<DashboardSkeleton />}>
        {DashboardComponent
          ? <DashboardComponent org={org} />
          : <DefaultDashboard   org={org} />}
      </Suspense>
    </JARVISPageShell>
  );
}

export default PersonalizedDashboard;
