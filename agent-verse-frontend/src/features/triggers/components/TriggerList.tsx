import { useState } from 'react';
import { Clock, Link, MessageSquare, Database, Activity, Cpu, ChevronDown, ChevronUp, Zap, Search, Filter } from 'lucide-react';
import type { Trigger, TriggerFamily } from '../types';
import { TRIGGER_FAMILY_LABELS, TRIGGER_TYPE_FAMILY } from '../types';
import { TriggerCard } from './TriggerCard';

interface TriggerListProps {
  triggers: Trigger[];
  isLoading: boolean;
}

const FAMILY_ICONS: Record<TriggerFamily, React.ReactNode> = {
  time: <Clock className="h-3.5 w-3.5" />,
  goal_chain: <Zap className="h-3.5 w-3.5" />,
  conversational: <MessageSquare className="h-3.5 w-3.5" />,
  webhook: <Link className="h-3.5 w-3.5" />,
  data: <Database className="h-3.5 w-3.5" />,
  monitoring: <Activity className="h-3.5 w-3.5" />,
  state_condition: <Filter className="h-3.5 w-3.5" />,
  ml_signal: <Cpu className="h-3.5 w-3.5" />,
  iot: <Cpu className="h-3.5 w-3.5" />,
};

export function TriggerList({ triggers, isLoading }: TriggerListProps) {
  const [search, setSearch] = useState('');
  const [familyFilter, setFamilyFilter] = useState<TriggerFamily | 'all'>('all');
  const [expandedFamily, setExpandedFamily] = useState<TriggerFamily | null>(null);

  const filtered = triggers.filter((t) => {
    const matchSearch =
      !search ||
      t.goal_template.toLowerCase().includes(search.toLowerCase()) ||
      t.spec.trigger_type.toLowerCase().includes(search.toLowerCase()) ||
      (t.spec.description ?? '').toLowerCase().includes(search.toLowerCase());
    const family = TRIGGER_TYPE_FAMILY[t.spec.trigger_type];
    const matchFamily = familyFilter === 'all' || family === familyFilter;
    return matchSearch && matchFamily;
  });

  // Group by family
  const grouped = filtered.reduce<Partial<Record<TriggerFamily, Trigger[]>>>((acc, t) => {
    const family = TRIGGER_TYPE_FAMILY[t.spec.trigger_type] ?? 'time';
    if (!acc[family]) acc[family] = [];
    acc[family]!.push(t);
    return acc;
  }, {});

  const families = Object.keys(grouped) as TriggerFamily[];

  if (isLoading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-16 rounded-xl bg-muted animate-pulse" />
        ))}
      </div>
    );
  }

  if (triggers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center text-muted-foreground">
        <Zap className="h-12 w-12 mb-4 opacity-20" />
        <p className="text-lg font-medium">No triggers yet</p>
        <p className="text-sm mt-1">Create a trigger to automate goal execution.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Search + Filter */}
      <div className="flex gap-3 flex-wrap">
        <div className="relative flex-1 min-w-48">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search triggers…"
            className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-border bg-background focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        <select
          value={familyFilter}
          onChange={(e) => setFamilyFilter(e.target.value as TriggerFamily | 'all')}
          className="rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="all">All families</option>
          {(Object.keys(TRIGGER_FAMILY_LABELS) as TriggerFamily[]).map((f) => (
            <option key={f} value={f}>{TRIGGER_FAMILY_LABELS[f]}</option>
          ))}
        </select>
      </div>

      {/* Grouped list */}
      {families.length === 0 && (
        <p className="text-sm text-muted-foreground text-center py-8">No results match your filter.</p>
      )}
      {families.map((family) => {
        const items = grouped[family]!;
        const isExpanded = expandedFamily === family || families.length === 1;
        return (
          <div key={family} className="rounded-xl border border-border bg-card overflow-hidden">
            <button
              onClick={() => setExpandedFamily(isExpanded ? null : family)}
              className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium hover:bg-muted/50 transition-colors"
            >
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground">{FAMILY_ICONS[family]}</span>
                <span>{TRIGGER_FAMILY_LABELS[family]}</span>
                <span className="rounded-full bg-muted text-muted-foreground text-xs px-2 py-0.5">
                  {items.length}
                </span>
              </div>
              {isExpanded ? (
                <ChevronUp className="h-4 w-4 text-muted-foreground" />
              ) : (
                <ChevronDown className="h-4 w-4 text-muted-foreground" />
              )}
            </button>
            {isExpanded && (
              <div className="border-t border-border divide-y divide-border">
                {items.map((trigger) => (
                  <TriggerCard key={trigger.schedule_id} trigger={trigger} />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
