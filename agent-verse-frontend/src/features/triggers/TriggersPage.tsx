import { useState } from 'react';
import { Plus, AlertCircle } from 'lucide-react';
import { TriggerList } from './components/TriggerList';
import { TriggerCreateModal } from './components/TriggerCreateModal';
import { TriggerDLQPanel } from './components/TriggerDLQPanel';
import { useTriggers } from './hooks';

type ActiveTab = 'all' | 'dlq';

export function TriggersPage() {
  const [showCreate, setShowCreate] = useState(false);
  const [activeTab, setActiveTab] = useState<ActiveTab>('all');
  const { data: triggers, isLoading, isError } = useTriggers();

  const pausedCount = triggers?.filter((t: { paused: boolean }) => t.paused).length ?? 0;

  return (
    <div className="flex flex-col gap-6 p-6 max-w-screen-xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Triggers</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Automate goals across time, events, webhooks, channels, IoT, and more.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          <Plus className="h-4 w-4" />
          New Trigger
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard label="Total" value={triggers?.length ?? 0} icon="⚡" />
        <StatCard label="Active" value={(triggers?.filter((t: { paused: boolean }) => !t.paused).length) ?? 0} icon="🟢" />
        <StatCard label="Paused" value={pausedCount} icon="⏸" />
        <StatCard label="Fired Today" value="—" icon="🔥" />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border">
        {(['all', 'dlq'] as ActiveTab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium rounded-t border-b-2 transition-colors ${
              activeTab === tab
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {tab === 'all' ? 'All Triggers' : 'Dead Letter Queue'}
            {tab === 'dlq' && (
              <span className="ml-1.5 rounded-full bg-destructive text-destructive-foreground text-xs px-1.5 py-0.5">
                ⚠
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      {isError && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          Failed to load triggers. Check your connection and try again.
        </div>
      )}

      {activeTab === 'all' && (
        <TriggerList triggers={triggers ?? []} isLoading={isLoading} />
      )}
      {activeTab === 'dlq' && <TriggerDLQPanel />}

      {/* Create modal */}
      {showCreate && <TriggerCreateModal onClose={() => setShowCreate(false)} />}
    </div>
  );
}

function StatCard({ label, value, icon }: { label: string; value: number | string; icon: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{label}</span>
        <span className="text-base">{icon}</span>
      </div>
      <div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div>
    </div>
  );
}
