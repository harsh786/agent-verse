/**
 * PatternSelectionPanel — the agent-pattern selection surface (WS-11 gap).
 *
 * Shows the pattern this goal was auto-routed to (or explicitly overridden with),
 * a plain-language explanation of *why*, and a control to override it — choose a
 * different pattern explicitly and re-run the goal. Wired to the real backend
 * endpoint GET /goals/{id}/pattern-selection; no fabricated data.
 */
import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Brain, Users, ShieldCheck, GitBranch, Info, Lock, Wand2, ArrowRight } from 'lucide-react';
import { goalsApi, type PatternSelectionResponse } from '@/lib/api/client';
import { Skeleton } from '@/components/ui/Skeleton';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { toast } from '@/stores/toast';

interface Props {
  goalId: string;
  goalText?: string;
}

const CATEGORY_META: Record<string, { label: string; icon: React.ReactNode }> = {
  reasoning: { label: 'Reasoning', icon: <Brain className="h-3.5 w-3.5" aria-hidden /> },
  multi_agent: { label: 'Coordination', icon: <Users className="h-3.5 w-3.5" aria-hidden /> },
  safety: { label: 'Safety', icon: <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> },
};

export function PatternSelectionPanel({ goalId, goalText }: Props) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [overrideId, setOverrideId] = useState<string>('');

  const { data, isLoading, error } = useQuery<PatternSelectionResponse>({
    queryKey: ['goal-pattern-selection', goalId],
    queryFn: () => goalsApi.getPatternSelection(goalId),
    enabled: !!goalId,
    staleTime: 30_000,
  });

  const overrideMutation = useMutation({
    mutationFn: (strategy: string) =>
      goalsApi.submit({ goal: goalText ?? data?.primary_pattern ?? '', strategy_override: strategy }),
    onSuccess: (res) => {
      toast({ kind: 'success', message: 'Re-running with the selected pattern.' });
      void qc.invalidateQueries({ queryKey: ['goals'] });
      navigate(`/goals/${res.id ?? res.goal_id}`);
    },
    onError: () => toast({ kind: 'error', message: 'Could not re-run with that pattern.' }),
  });

  const overridable = useMemo(
    () => (data?.available_patterns ?? []).filter((p) => p.available),
    [data],
  );

  if (isLoading) {
    return (
      <div className="space-y-3" aria-label="Loading pattern selection" aria-busy="true">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="text-sm text-muted-foreground text-center py-8" data-testid="pattern-empty">
        No pattern selection is available for this goal yet.
      </div>
    );
  }

  const props = data.goal_properties;

  return (
    <div className="space-y-5 animate-in fade-in duration-300" data-testid="pattern-panel">
      {/* Primary pattern hero */}
      <section
        aria-labelledby="pattern-primary-heading"
        className="rounded-xl border border-border bg-card p-4"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1.5">
              <GitBranch className="h-3.5 w-3.5" aria-hidden /> Selected Pattern
            </p>
            <h3
              id="pattern-primary-heading"
              className="mt-1 text-lg font-semibold"
              data-testid="pattern-primary-name"
            >
              {data.primary_pattern_name}
            </h3>
          </div>
          <Badge
            variant={data.source === 'override' ? 'default' : 'secondary'}
            data-testid="pattern-source"
          >
            {data.source === 'override' ? 'Overridden' : 'Auto-selected'}
          </Badge>
        </div>

        {/* Goal property chips — the signals that drove the choice */}
        <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Goal characteristics">
          <PropChip label="complexity" value={props.complexity} />
          <PropChip label="domain" value={props.domain} />
          <PropChip label="risk" value={props.risk} />
          {props.requires_code && <PropChip label="code" value="yes" />}
          <PropChip label="autonomy" value={data.autonomy_mode} />
        </div>

        {data.advanced_tier_gated && data.multi_agent_patterns.some((p) => p !== 'single_agent') && (
          <p
            className="mt-3 flex items-start gap-1.5 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-500"
            role="note"
            data-testid="pattern-gated-note"
          >
            <Lock className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" aria-hidden />
            <span>
              The advanced multi-agent tier is selected for this goal but gated off by default
              (safety). Selection is recorded; enable the tier to let it run.
            </span>
          </p>
        )}
      </section>

      {/* Why — plain-language rationale grouped by category */}
      <section aria-labelledby="pattern-why-heading">
        <h3
          id="pattern-why-heading"
          className="text-xs font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1.5 mb-2"
        >
          <Info className="h-3.5 w-3.5" aria-hidden /> Why this pattern
        </h3>
        <ul className="space-y-1.5" role="list" data-testid="pattern-rationale">
          {data.rationale.map((r) => {
            const meta = CATEGORY_META[r.category] ?? CATEGORY_META.reasoning;
            return (
              <li
                key={`${r.category}-${r.pattern}`}
                className="flex items-start gap-2 rounded-lg border border-border bg-card p-2.5 text-xs"
              >
                <span className="mt-0.5 text-primary" aria-hidden>
                  {meta.icon}
                </span>
                <span>
                  <span className="font-medium">{r.name}</span>
                  <span className="text-muted-foreground"> · {meta.label}</span>
                  <span className="block text-muted-foreground">{r.why}</span>
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      {/* Override control — choose a pattern explicitly and re-run */}
      <section
        aria-labelledby="pattern-override-heading"
        className="rounded-xl border border-border bg-card p-4"
      >
        <h3
          id="pattern-override-heading"
          className="text-xs font-semibold uppercase tracking-wide text-muted-foreground flex items-center gap-1.5 mb-2"
        >
          <Wand2 className="h-3.5 w-3.5" aria-hidden /> Override
        </h3>
        <p className="text-xs text-muted-foreground mb-3">
          Re-run this goal with a pattern you choose. The registry decides what is available.
        </p>
        <div className="flex flex-col sm:flex-row gap-2">
          <label htmlFor="pattern-override-select" className="sr-only">
            Choose a pattern to override with
          </label>
          <select
            id="pattern-override-select"
            data-testid="pattern-override-select"
            className="flex-1 h-9 rounded-lg border border-border bg-background px-3 text-sm"
            value={overrideId}
            onChange={(e) => setOverrideId(e.target.value)}
          >
            <option value="">Choose a pattern…</option>
            {overridable.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {p.description}
              </option>
            ))}
          </select>
          <Button
            variant="default"
            size="default"
            disabled={!overrideId || overrideMutation.isPending}
            onClick={() => overrideId && overrideMutation.mutate(overrideId)}
            data-testid="pattern-override-run"
          >
            {overrideMutation.isPending ? 'Starting…' : 'Run with this pattern'}
            <ArrowRight className="h-3.5 w-3.5 ml-1" aria-hidden />
          </Button>
        </div>
      </section>
    </div>
  );
}

function PropChip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-[11px]">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </span>
  );
}
