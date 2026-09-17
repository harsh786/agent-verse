import { useState } from 'react';
import { Shield, Lock, Eye, ClipboardList, Key, BarChart3 } from 'lucide-react';
import { AgentIdentityPanel } from './panels/AgentIdentityPanel';
import { GovernancePanel } from './panels/GovernancePanel';
import { GuardrailsPanel } from './panels/GuardrailsPanel';
import { AuditPanel } from './panels/AuditPanel';
import { ScopesPanel } from './panels/ScopesPanel';
import { LimitsPanel } from './panels/LimitsPanel';
import { MissionControlLayout } from '@/components/ui/MissionControlLayout';
import { JARVISPageShell, JARVISStagger } from '@/components/ui/JARVISPageShell';

const TABS = [
  { id: 'identity',    label: 'Agent Identity', icon: Lock,          description: 'Per-agent keys, capability manifests, delegation lineage' },
  { id: 'governance',  label: 'Governance',      icon: Shield,        description: 'Policies, HITL approvals, compliance bundles, time rules' },
  { id: 'guardrails',  label: 'Guardrails',      icon: Eye,           description: 'Injection detection, encoding attacks, domain policies' },
  { id: 'audit',       label: 'Audit Trail',     icon: ClipboardList, description: 'Immutable hash chain, export, tamper verification' },
  { id: 'scopes',      label: 'Scopes & Roles',  icon: Key,           description: 'Custom roles, scope inheritance, temporary elevation' },
  { id: 'limits',      label: 'Limits',          icon: BarChart3,     description: 'Rate limits, token budgets, step counts, storage quotas' },
] as const;

type TabId = typeof TABS[number]['id'];

export function SecurityScore({ score }: { score: number }) {
  const isGood    = score >= 80;
  const isMedium  = score >= 60;
  const color     = isGood ? 'text-verified-green' : isMedium ? 'text-risk-amber' : 'text-mission-red';
  const bgBorder  = isGood
    ? 'bg-verified-green/15 border-verified-green/30'
    : isMedium
      ? 'bg-risk-amber/15 border-risk-amber/30'
      : 'bg-mission-red/15 border-mission-red/30';
  const pulseDot  = isGood ? 'bg-verified-green' : isMedium ? 'bg-risk-amber' : 'bg-mission-red';

  return (
    <div className={`flex items-center gap-3 px-4 py-2 rounded-full border ${bgBorder}`}>
      <span className={`w-2 h-2 rounded-full animate-pulse ${pulseDot}`} />
      <div className={`text-2xl font-bold font-mono ${color}`}>{score}</div>
      <div className={`text-xs font-medium ${color}`}>Security Score</div>
    </div>
  );
}

export default function SecurityCenterPage() {
  const [activeTab, setActiveTab] = useState<TabId>('identity');

  return (
    <JARVISPageShell>
    <MissionControlLayout>
      <JARVISStagger className="space-y-0" data-testid="security-center-page">
        {/* Header */}
        <div className="pb-0">
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-3">
              <div className="p-2.5 rounded-lg bg-neural-violet/20 border border-neural-violet/30 shadow-lg shadow-neural-violet/10">
                <Shield className="h-5 w-5 text-neural-violet" />
              </div>
              <div>
                <h1 className="text-2xl font-bold text-[#00D4FF] tracking-tight">Security Center</h1>
                <p className="text-white/40 text-sm mt-0.5">
                  Agent identity, governance, guardrails, audit, scopes, and limits
                </p>
              </div>
            </div>
            <SecurityScore score={78} />
          </div>

          {/* Tab navigation */}
          <div className="flex gap-1 overflow-x-auto pb-1">
            {TABS.map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  data-testid={`tab-${tab.id}`}
                  onClick={() => setActiveTab(tab.id)}
                  title={tab.description}
                  className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-[color,background-color,border-color,opacity,box-shadow,transform] border ${
                    activeTab === tab.id
                      ? 'bg-neural-violet text-white border-neural-violet shadow-lg shadow-neural-violet/20'
                      : 'text-white/50 border-neural-violet/15 hover:bg-neural-violet/10 hover:text-white/80 hover:border-neural-violet/30'
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  {tab.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Tab content separator */}
        <div className="h-px bg-neural-violet/15 mb-5 mt-3" />

        {/* Tab content */}
        <div data-testid="tab-content">
          {activeTab === 'identity'   && <AgentIdentityPanel />}
          {activeTab === 'governance' && <GovernancePanel />}
          {activeTab === 'guardrails' && <GuardrailsPanel />}
          {activeTab === 'audit'      && <AuditPanel />}
          {activeTab === 'scopes'     && <ScopesPanel />}
          {activeTab === 'limits'     && <LimitsPanel />}
        </div>
      </JARVISStagger>
    </MissionControlLayout>
    </JARVISPageShell>
  );
}
