import { useEffect, useRef, useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  Brain,
  GripVertical,
  Info,
  KeyRound,
  Loader2,
  Pencil,
  Plug,
  Plus,
  RotateCcw,
  Save,
  Trash2,
  Zap,
} from 'lucide-react';
import type { CapabilityGroup, ConfiguredModel } from '@/lib/api/client';
import { Badge } from './Badge';
import { STATE_CLASSES, TEXT_TONE } from './badgeStyles';
import { canLead, coverageOf, keyOf, skipReason, usable, type CapabilityMeta } from './registryRows';

/** host:port of an endpoint URL for compact display; the raw value if it does not parse. */
const endpointHost = (url: string) => {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
};

interface Props {
  cap: CapabilityMeta;
  group: CapabilityGroup | undefined;
  draft: string[] | undefined;
  canModify: boolean;
  denyTitle: string;
  busy: boolean;
  saving: boolean;
  orderError?: string;
  onReorder: (order: string[]) => void;
  onSave: (order: string[]) => void;
  onDiscard: () => void;
  onReset: () => void;
  onEdit: (m: ConfiguredModel) => void;
  onRemove: (m: ConfiguredModel) => void;
  onAdd: (capability: string) => void;
}

/**
 * One capability's models in execution order, with the preference-order
 * editor: drag and drop, the up / down buttons, or the keyboard (focus the
 * grip, then Arrow Up / Down). Unsaved changes are flagged until saved or
 * discarded. A refused or not-servable model is shown disabled with the
 * reason and can never be moved into first place.
 */
export function CapabilitySection({
  cap, group, draft, canModify, denyTitle, busy, saving, orderError,
  onReorder, onSave, onDiscard, onReset, onEdit, onRemove, onAdd,
}: Props) {
  const serverModels = group?.models ?? [];
  const byKey = new Map(serverModels.map((m) => [keyOf(m), m]));
  const models = draft
    ? draft.map((k) => byKey.get(k)).filter((m): m is ConfiguredModel => !!m)
    : serverModels;
  const order = models.map(keyOf);
  const dirty = !!draft && draft.join('|') !== serverModels.map(keyOf).join('|');

  const [announcement, setAnnouncement] = useState('');
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);
  const handleRefs = useRef(new Map<string, HTMLButtonElement>());
  const [focusKey, setFocusKey] = useState<string | null>(null);

  useEffect(() => {
    if (!focusKey) return;
    handleRefs.current.get(focusKey)?.focus();
    setFocusKey(null);
  }, [focusKey, draft]);

  /** Move a model; refused when it would put a model that cannot lead in first place. */
  const move = (from: number, to: number, refocus = false): boolean => {
    if (to < 0 || to >= models.length || from === to) return false;
    const item = models[from];
    if (!canLead(item)) return false;
    const next = [...models];
    next.splice(from, 1);
    next.splice(to, 0, item);
    onReorder(next.map(keyOf));
    setAnnouncement(`${item.model_id} moved to position ${to + 1} of ${models.length}.`);
    if (refocus) setFocusKey(keyOf(item));
    return true;
  };

  // Primary/fallback badges: the server's view when the order is saved, a
  // local preview (first model that can lead, then the next ones) while an
  // unsaved reordering is pending.
  let primaryIdx = -1;
  const fallbackNo = new Map<number, number>();
  if (dirty) {
    let n = 0;
    models.forEach((m, i) => {
      if (!canLead(m)) return;
      if (primaryIdx === -1) primaryIdx = i;
      else fallbackNo.set(i, ++n);
    });
  } else if (group) {
    primaryIdx = models.findIndex((m) => m.model_id === group.selected_model_id && !m.refused);
    // A refused row (e.g. wrong embedding dimension) is never badged primary.
    if (primaryIdx === -1) primaryIdx = models.findIndex((m) => m.rank === 1 && !m.refused);
    models.forEach((m, i) => {
      const fb = group.fallback_model_ids.indexOf(m.model_id);
      if (fb !== -1 && i !== primaryIdx) fallbackNo.set(i, fb + 1);
    });
  }
  const anyNotReady = models.some((m) => !m.provider_ready);
  const anyNotServed = models.some((m) => m.provider_ready && m.servable === false);
  const coverage = coverageOf(group);
  const reorderable = canModify && models.length > 1;
  const instructionsId = `reorder-help-${cap.key}`;

  return (
    <section
      aria-labelledby={`cap-title-${cap.key}`}
      data-testid={`capability-${cap.key}`}
      data-dirty={dirty ? 'true' : 'false'}
      className={`rounded-2xl border bg-card p-5 ${dirty ? STATE_CLASSES.dirtySection : 'border-border'}`}
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 id={`cap-title-${cap.key}`} className="text-lg font-semibold">{cap.label}</h2>
          <p className="text-xs text-muted-foreground">{cap.hint}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {dirty && (
            <Badge tone="warning" testId={`dirty-${cap.key}`}>Unsaved order</Badge>
          )}
          {group && group.models.length > 0 && (
            <Badge tone="neutral">{group.order_mode === 'preference' ? 'Preference order' : 'Cheapest first'}</Badge>
          )}
          {coverage.state === 'not_ready' && (
            <Badge tone="warning" title="Models are listed, but none of them can serve this category right now">
              Not usable
            </Badge>
          )}
          <span data-testid={`capability-count-${cap.key}`}>
            {group && group.models.length > 0
              ? `${coverage.ready} of ${models.length} ready`
              : `${models.length} configured`}
          </span>
        </div>
      </div>
      {group?.note && models.length > 0 && (
        <p className="mb-3 flex items-start gap-1.5 text-xs text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> {group.note}
        </p>
      )}
      {models.length === 0 ? (
        <div
          data-testid={`empty-${cap.key}`}
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-border px-4 py-4"
        >
          <p className="text-sm text-muted-foreground">
            No {cap.noun} yet — add one{canModify ? '' : ' (a platform operator can add it)'}.
          </p>
          {canModify && (
            <button
              type="button"
              onClick={() => onAdd(cap.key)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add {cap.noun}
            </button>
          )}
        </div>
      ) : (
        <>
          {reorderable && (
            <p id={instructionsId} className="sr-only">
              Press Arrow Up or Arrow Down to move this model in the order. Models that cannot serve
              cannot be moved into first place.
            </p>
          )}
          <ol className="space-y-2" aria-label={`${cap.label} preference order`}>
            {models.map((m, i) => {
              const primary = i === primaryIdx;
              const fb = fallbackNo.get(i);
              const k = keyOf(m);
              const reason = skipReason(m);
              const lead = canLead(m);
              const draggable = reorderable && lead && !busy;
              const upBlocked = i === 0 || busy || !lead;
              const downBlocked = i === models.length - 1 || busy || !lead;
              return (
                <li
                  key={k}
                  data-testid={`model-row-${k}`}
                  aria-disabled={lead ? undefined : true}
                  draggable={draggable}
                  onDragStart={(e) => {
                    setDragKey(k);
                    e.dataTransfer.effectAllowed = 'move';
                    e.dataTransfer.setData('text/plain', k);
                  }}
                  onDragOver={(e) => {
                    if (!dragKey) return;
                    e.preventDefault();
                    setOverIdx(i);
                  }}
                  onDragLeave={() => setOverIdx((v) => (v === i ? null : v))}
                  onDrop={(e) => {
                    e.preventDefault();
                    const from = order.indexOf(dragKey ?? e.dataTransfer.getData('text/plain'));
                    if (from !== -1) move(from, i);
                    setDragKey(null);
                    setOverIdx(null);
                  }}
                  onDragEnd={() => { setDragKey(null); setOverIdx(null); }}
                  className={`flex flex-col gap-2 rounded-xl border px-3 py-3 sm:flex-row sm:items-center sm:justify-between ${
                    primary
                      ? STATE_CLASSES.primaryRow
                      : 'border-border bg-background'
                  } ${usable(m) && !m.refused ? '' : 'opacity-60'} ${
                    overIdx === i && dragKey && dragKey !== k ? 'ring-2 ring-primary' : ''
                  } ${dragKey === k ? 'opacity-50' : ''}`}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    {reorderable && (
                      <button
                        type="button"
                        ref={(el) => {
                          if (el) handleRefs.current.set(k, el);
                          else handleRefs.current.delete(k);
                        }}
                        aria-label={`Reorder ${m.model_id}, position ${i + 1} of ${models.length}`}
                        aria-describedby={instructionsId}
                        aria-disabled={lead && !busy ? undefined : true}
                        title={lead ? 'Drag, or use Arrow Up / Down' : `Cannot be moved first: ${reason}`}
                        onKeyDown={(e) => {
                          if (!lead || busy) return;
                          if (e.key === 'ArrowUp') { e.preventDefault(); move(i, i - 1, true); }
                          if (e.key === 'ArrowDown') { e.preventDefault(); move(i, i + 1, true); }
                        }}
                        className={`rounded p-1 text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring ${
                          lead && !busy ? 'cursor-grab hover:bg-muted' : 'cursor-not-allowed opacity-40'
                        }`}
                      >
                        <GripVertical className="h-4 w-4" aria-hidden="true" />
                      </button>
                    )}
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">
                      {i + 1}
                    </span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="truncate font-medium" title={m.display_name && m.display_name !== m.model_id ? m.display_name : undefined}>
                          {m.model_id}
                        </span>
                        {primary && (
                          <Badge tone="solid-success"><Zap className="h-3 w-3" aria-hidden="true" /> Primary</Badge>
                        )}
                        {fb !== undefined && <Badge tone="info">Fallback {fb}</Badge>}
                        {m.refused && (
                          <Badge tone="danger" title={m.refusal_reason || undefined}>Refused</Badge>
                        )}
                        {cap.key === 'embedding' && m.collection_compatible && m.dimensions ? (
                          <Badge tone="info" title={`Knowledge collections of ${m.dimensions}-d can be bound to this model`}>
                            {m.dimensions}-d collections
                          </Badge>
                        ) : null}
                        {!m.provider_ready && (
                          <Badge tone="warning" title="This provider has no API key configured, so the model is skipped at runtime">
                            No API key
                          </Badge>
                        )}
                        {m.provider_ready && m.servable === false && (
                          <Badge tone="warning" title="Named in the server configuration, but no API key or endpoint is configured to serve it">
                            Not served
                          </Badge>
                        )}
                        {m.has_api_key ? (
                          <Badge tone="success" title="This model has its own API key saved (encrypted; never shown)">
                            <KeyRound className="h-3 w-3" aria-hidden="true" /> Key saved
                          </Badge>
                        ) : usable(m) && (
                          <Badge tone="neutral" title="No key saved with this model: the provider's key configured on the server is used">
                            Provider key
                          </Badge>
                        )}
                        {cap.key === 'text_generation' && (
                          <Badge
                            tone={m.thinking === 'off' ? 'primary' : m.thinking === 'on' ? 'info' : 'neutral'}
                            testId={`thinking-badge-${k}`}
                            title="Thinking (reasoning) setting for this model"
                          >
                            <Brain className="h-3 w-3" aria-hidden="true" />
                            Thinking {m.thinking ?? 'auto'}
                            {m.thinking === 'on' && m.thinking_budget_tokens ? ` · ${m.thinking_budget_tokens} tok` : ''}
                          </Badge>
                        )}
                        {m.source === 'env' && (
                          <Badge tone="neutral" title="Seeded from environment configuration">env</Badge>
                        )}
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {m.provider} · ${m.cost_per_1k_input.toFixed(5)}/1k in
                        {m.cost_per_1k_input === 0 && ' (self-hosted / free)'}
                        {m.supports_tools && ' · tools'}
                        {m.supports_vision && ' · vision'}
                        {m.output_dimensions ? ` · ${m.output_dimensions}-d requested` : ''}
                        {m.base_url && (
                          <>
                            {' · '}
                            <span
                              title={m.base_url}
                              aria-label={`Endpoint ${m.base_url}`}
                              className="inline-flex items-center gap-1 font-mono"
                            >
                              <Plug className="h-3 w-3" aria-hidden="true" />
                              {endpointHost(m.base_url)}
                            </span>
                          </>
                        )}
                      </div>
                      {reason && (
                        <p data-testid={`skip-reason-${k}`} className={`mt-0.5 text-xs ${m.refused ? TEXT_TONE.danger : TEXT_TONE.warning}`}>
                          {m.refused && cap.key === 'embedding'
                            ? 'Cannot be the default embedder'
                            : 'Skipped — cannot be primary'}: {reason}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1 self-end sm:ml-3 sm:self-auto">
                    {reorderable && (
                      <>
                        <button
                          type="button"
                          onClick={() => move(i, i - 1)}
                          disabled={upBlocked}
                          aria-label={`Move ${m.model_id} up`}
                          title={lead ? 'Move up' : `Cannot be moved: ${reason}`}
                          className="rounded-lg p-2 text-muted-foreground hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-30"
                        >
                          <ArrowUp className="h-4 w-4" aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          onClick={() => move(i, i + 1)}
                          disabled={downBlocked}
                          aria-label={`Move ${m.model_id} down`}
                          title={lead ? 'Move down' : `Cannot be moved: ${reason}`}
                          className="rounded-lg p-2 text-muted-foreground hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-30"
                        >
                          <ArrowDown className="h-4 w-4" aria-hidden="true" />
                        </button>
                      </>
                    )}
                    {canModify && (
                      <button
                        type="button"
                        onClick={() => onEdit(m)}
                        aria-label={`Edit ${m.model_id}`}
                        title="Edit model"
                        className="rounded-lg p-2 text-muted-foreground hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
                      >
                        <Pencil className="h-4 w-4" aria-hidden="true" />
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => onRemove(m)}
                      disabled={!canModify}
                      title={canModify ? 'Remove model' : denyTitle}
                      className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-40"
                      aria-label={`Remove ${m.model_id}`}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </div>
                </li>
              );
            })}
          </ol>
          <p aria-live="polite" className="sr-only" data-testid={`reorder-announcement-${cap.key}`}>
            {announcement}
          </p>
        </>
      )}
      {anyNotReady && (
        <p className="mt-2 text-xs text-muted-foreground">
          Models marked “No API key” are skipped at runtime until their provider key is set
          or the model is saved with its own API key or endpoint URL.
        </p>
      )}
      {anyNotServed && (
        <p className="mt-2 text-xs text-muted-foreground">
          Models marked “Not served” are named in the server configuration, but nothing is
          configured to call them; add the model here with its endpoint URL or API key.
        </p>
      )}
      {group && canModify && models.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => onSave(order)}
            disabled={!dirty || busy}
            aria-label={`Save ${cap.label} order`}
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Save className="h-3.5 w-3.5" aria-hidden="true" />}
            Save order
          </button>
          {dirty && (
            <button
              type="button"
              onClick={onDiscard}
              disabled={busy}
              className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            >
              Discard changes
            </button>
          )}
          <button
            type="button"
            onClick={onReset}
            disabled={busy || (group.order_mode === 'cost' && !dirty)}
            aria-label={`Reset ${cap.label} to cost order`}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Reset to cost order
          </button>
        </div>
      )}
      {orderError && (
        <p role="alert" className="mt-2 text-xs text-destructive">{orderError}</p>
      )}
    </section>
  );
}
