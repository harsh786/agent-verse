import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Route, TriangleAlert } from 'lucide-react';
import { Skeleton } from '@/components/ui/Skeleton';
import {
  modelsApi,
  type CapabilityResolution,
  type ResolvedModelRef,
  type RoleResolution,
} from '@/lib/api/client';
import { Badge } from './Badge';
import { PANEL_CLASSES, TEXT_TONE, type BadgeTone } from './badgeStyles';

const SOURCE_TONE: Record<string, BadgeTone> = {
  tenant_pin: 'primary',
  registry_order: 'success',
  registry_cheapest: 'info',
  deployment_profile: 'neutral',
  env_pin: 'neutral',
  local_default: 'neutral',
  default: 'warning',
  none: 'danger',
};

const SOURCE_HELP: Record<string, string> = {
  tenant_pin: "Your tenant's routing policy pins this role (PUT /models/routing-policies).",
  registry_order: 'The first usable model of the saved preference order below.',
  registry_cheapest: 'No order is saved: the cheapest usable registered model.',
  deployment_profile: "The deployment's role map (hybrid / on-prem / NVIDIA profile).",
  env_pin: 'Pinned by the server environment (e.g. DEFAULT_PLANNING_MODEL, VISION_MODEL).',
  local_default: 'A local in-process tier (Tesseract OCR, the local cross-encoder).',
  default: "Nothing more specific: the provider's default model.",
  none: 'Nothing resolves.',
};

function ModelRef({ m }: { m: ResolvedModelRef | null }) {
  if (!m) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1.5">
      <span className="truncate font-mono text-xs font-medium" title={m.model_id}>{m.model_id}</span>
      {m.provider && <span className="text-[11px] text-muted-foreground">{m.provider}</span>}
      {m.servable === false && <Badge tone="warning" title="Nothing in this deployment serves this model">Not served</Badge>}
    </span>
  );
}

function Fallbacks({ items }: { items: ResolvedModelRef[] }) {
  if (items.length === 0) return <span className="text-muted-foreground">none</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((f, i) => (
        <span
          key={`${f.model_id}-${f.provider}-${i}`}
          className="whitespace-nowrap rounded border border-border px-1.5 py-0.5 font-mono text-[11px]"
          title={f.provider ? `${f.model_id} on ${f.provider}` : f.model_id}
        >
          {f.model_id}
          {f.provider && <span className="text-muted-foreground"> @{f.provider}</span>}
        </span>
      ))}
    </span>
  );
}

function SourceBadge({ source, label }: { source: string; label: string }) {
  return (
    <Badge tone={SOURCE_TONE[source] ?? 'neutral'} title={SOURCE_HELP[source]} testId="resolution-source">
      {label || source}
    </Badge>
  );
}

function CapabilityRow({ c }: { c: CapabilityResolution }) {
  return (
    <tr data-testid={`resolution-cap-${c.capability}`} className="border-t border-border align-top">
      <th scope="row" className="py-2 pr-3 text-left text-sm font-medium">{c.label}</th>
      {c.routed ? (
        <>
          <td className="py-2 pr-3"><ModelRef m={c.model} /></td>
          <td className="py-2 pr-3"><SourceBadge source={c.source} label={c.source_label} /></td>
          <td className="py-2 pr-3"><Fallbacks items={c.fallbacks} /></td>
        </>
      ) : (
        <td colSpan={3} className="py-2 pr-3 text-xs text-muted-foreground" data-testid="resolution-not-routed">
          Not routed by the registry yet — this capability still uses its own configuration.
        </td>
      )}
      <td className="py-2 text-xs">
        {c.warning && (
          <span className={`flex items-start gap-1 ${TEXT_TONE.warning}`}>
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{c.warning}</span>
          </span>
        )}
        {c.note && <span className="block text-muted-foreground">{c.note}</span>}
      </td>
    </tr>
  );
}

function RoleRow({ r }: { r: RoleResolution }) {
  return (
    <tr data-testid={`resolution-role-${r.task_type}`} className="border-t border-border align-top">
      <th scope="row" className="py-2 pr-3 text-left text-sm font-medium">
        {r.label}
        <span className="mt-0.5 block text-[11px] font-normal text-muted-foreground" title={r.roles.join(', ')}>
          {r.roles.length} role{r.roles.length === 1 ? '' : 's'}: {r.roles.slice(0, 4).join(', ')}
          {r.roles.length > 4 ? ` +${r.roles.length - 4}` : ''}
        </span>
      </th>
      <td className="py-2 pr-3"><ModelRef m={r.model} /></td>
      <td className="py-2 pr-3"><SourceBadge source={r.source} label={r.source_label} /></td>
      <td className="py-2 pr-3"><Fallbacks items={r.fallbacks} /></td>
      <td className="py-2 text-xs">
        {r.warning && (
          <span className={`flex items-start gap-1 ${TEXT_TONE.warning}`}>
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{r.warning}</span>
          </span>
        )}
      </td>
    </tr>
  );
}

function TableHead({ first }: { first: string }) {
  return (
    <thead>
      <tr className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
        <th scope="col" className="w-40 pb-1 pr-3 font-medium">{first}</th>
        <th scope="col" className="pb-1 pr-3 font-medium">Model now</th>
        <th scope="col" className="pb-1 pr-3 font-medium">Source</th>
        <th scope="col" className="pb-1 pr-3 font-medium">Fallbacks</th>
        <th scope="col" className="pb-1 font-medium">Notes</th>
      </tr>
    </thead>
  );
}

/**
 * "Resolved models": what each capability and each agent role runs on right
 * now, where that choice comes from and what it fails over to
 * (GET /models/resolution — the runtime's own resolvers).
 */
export function ResolvedModelsPanel({ enabled }: { enabled: boolean }) {
  const [showRoles, setShowRoles] = useState(false);
  const q = useQuery({
    queryKey: ['models-resolution'],
    queryFn: () => modelsApi.resolution(),
    enabled,
    staleTime: 15_000,
  });
  const caps = q.data?.capabilities ?? [];
  const roles = q.data?.roles ?? [];
  const warnings = q.data?.warnings ?? [];
  const missing = caps.filter((c) => c.routed && !c.model);

  return (
    <section
      aria-labelledby="resolved-models-title"
      data-testid="resolved-models"
      className="rounded-2xl border border-border bg-card p-5"
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 id="resolved-models-title" className="flex items-center gap-2 text-lg font-semibold">
            <Route className="h-4 w-4" aria-hidden="true" /> Resolved models
          </h2>
          <p className="text-xs text-muted-foreground">
            What each capability and agent role uses right now, why, and what it fails over to.
          </p>
        </div>
      </div>
      {q.isLoading && (
        <div data-testid="resolution-loading" className="space-y-2" aria-busy="true">
          <span className="sr-only">Loading resolved models…</span>
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-7 w-full" />)}
        </div>
      )}
      {q.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load the resolved models: {(q.error as Error)?.message || 'request failed'}
        </p>
      )}
      {!q.isLoading && !q.isError && enabled && caps.length === 0 && (
        <p className="text-sm text-muted-foreground" data-testid="resolution-unavailable">
          This server does not report model resolution yet.
        </p>
      )}
      {caps.length > 0 && (
        <>
          {missing.length > 0 && (
            <div
              role="alert"
              data-testid="resolution-warnings"
              className={`mb-3 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${PANEL_CLASSES.warning}`}
            >
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <span>
                No usable model for {missing.map((c) => c.label).join(', ')}.
                {' '}{warnings.length > 0 ? warnings[0] : ''}
              </span>
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <caption className="sr-only">Model per capability</caption>
              <TableHead first="Capability" />
              <tbody>{caps.map((c) => <CapabilityRow key={c.capability} c={c} />)}</tbody>
            </table>
          </div>
          {roles.length > 0 && (
            <div className="mt-4">
              <button
                type="button"
                onClick={() => setShowRoles((v) => !v)}
                aria-expanded={showRoles}
                aria-controls="resolution-roles"
                className="inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-sm font-medium hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
              >
                {showRoles ? <ChevronDown className="h-4 w-4" aria-hidden="true" /> : <ChevronRight className="h-4 w-4" aria-hidden="true" />}
                Agent roles ({roles.reduce((n, r) => n + r.roles.length, 0)} roles in {roles.length} groups)
              </button>
              {showRoles && (
                <div id="resolution-roles" className="mt-2 overflow-x-auto">
                  <table className="w-full min-w-[640px] text-sm">
                    <caption className="sr-only">Model per agent role</caption>
                    <TableHead first="Role group" />
                    <tbody>{roles.map((r) => <RoleRow key={r.task_type} r={r} />)}</tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
