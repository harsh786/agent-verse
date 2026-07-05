import { useState } from 'react';
import { AgentIdentityPanel } from './panels/AgentIdentityPanel';
import { GovernancePanel } from './panels/GovernancePanel';
import { GuardrailsPanel } from './panels/GuardrailsPanel';
import { AuditPanel } from './panels/AuditPanel';
import { ScopesPanel } from './panels/ScopesPanel';
import { LimitsPanel } from './panels/LimitsPanel';

const TABS = [
  { id: 'identity', label: 'Agent Identity', icon: '🪪', description: 'Per-agent keys, capability manifests, delegation lineage' },
  { id: 'governance', label: 'Governance', icon: '⚖️', description: 'Policies, HITL approvals, compliance bundles, time rules' },
  { id: 'guardrails', label: 'Guardrails', icon: '🛡️', description: 'Injection detection, encoding attacks, domain policies' },
  { id: 'audit', label: 'Audit Trail', icon: '📋', description: 'Immutable hash chain, export, tamper verification' },
  { id: 'scopes', label: 'Scopes & Roles', icon: '🔑', description: 'Custom roles, scope inheritance, temporary elevation' },
  { id: 'limits', label: 'Limits', icon: '📊', description: 'Rate limits, token budgets, step counts, storage quotas' },
] as const;

type TabId = typeof TABS[number]['id'];

function SecurityScore({ score }: { score: number }) {
  const color = score >= 80 ? 'text-green-600 dark:text-green-400'
    : score >= 60 ? 'text-amber-600 dark:text-amber-400'
    : 'text-destructive';
  const bgColor = score >= 80 ? 'bg-green-100 dark:bg-green-900/30'
    : score >= 60 ? 'bg-amber-100 dark:bg-amber-900/30'
    : 'bg-destructive/10';
  return (
    <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full ${bgColor}`}>
      <div className={`text-2xl font-bold ${color}`}>{score}</div>
      <div className={`text-xs font-medium ${color}`}>Security Score</div>
    </div>
  );
}

export default function SecurityCenterPage() {
  const [activeTab, setActiveTab] = useState<TabId>('identity');

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="border-b bg-card">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
                <span className="text-2xl">🔐</span>
                Security Center
              </h1>
              <p className="text-muted-foreground mt-0.5 text-sm">
                Manage agent identity, governance, guardrails, audit, scopes, and limits
              </p>
            </div>
            <SecurityScore score={78} />
          </div>

          {/* Tab navigation */}
          <div className="flex gap-1 mt-4 overflow-x-auto">
            {TABS.map(tab => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors
                  ${activeTab === tab.id
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  }`}
              >
                <span>{tab.icon}</span>
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Tab content */}
      <div className="max-w-7xl mx-auto px-6 py-6">
        {activeTab === 'identity' && <AgentIdentityPanel />}
        {activeTab === 'governance' && <GovernancePanel />}
        {activeTab === 'guardrails' && <GuardrailsPanel />}
        {activeTab === 'audit' && <AuditPanel />}
        {activeTab === 'scopes' && <ScopesPanel />}
        {activeTab === 'limits' && <LimitsPanel />}
      </div>
    </div>
  );
}
