import { Brain, CircleCheck, Lightbulb, TriangleAlert, XCircle } from 'lucide-react';
import type { ModelEndpointTestResult, ModelProbeCheck, ThinkingMode } from '@/lib/api/client';
import { Badge } from './Badge';
import { PANEL_CLASSES, TEXT_TONE } from './badgeStyles';
import {
  PROBE_ERROR_COPY,
  PROBE_LABEL,
  probeErrorKind,
  recommendsThinkingOff,
} from './modelFormHelpers';

export type TestOutcome =
  | { sig: string; kind: 'result'; result: ModelEndpointTestResult }
  | { sig: string; kind: 'error'; message: string };

const MAX_SERVED_SHOWN = 8;

/** The per-capability checks; an older backend sends only the top-level probe. */
const checksOf = (r: ModelEndpointTestResult): ModelProbeCheck[] =>
  r.checks && r.checks.length > 0
    ? r.checks
    : [{
        probe: r.probe,
        capabilities: [],
        ok: r.ok,
        latency_ms: r.latency_ms,
        detail: r.detail,
        error: r.error,
        error_kind: r.error_kind ?? null,
        thinking: r.thinking,
        dimensions: r.dimensions,
        index_dimension: r.index_dimension,
        dimension_mismatch: r.dimension_mismatch,
        requested_dimensions: r.requested_dimensions,
        dimensions_ignored: r.dimensions_ignored,
      }];

function FailureHeadline({ message, kind }: { message: string; kind: ModelEndpointTestResult['error_kind'] }) {
  const copy = PROBE_ERROR_COPY[probeErrorKind(kind, message)];
  return (
    <span className="min-w-0 space-y-1 break-words">
      <span className="block font-semibold" data-testid="endpoint-test-error-title">{copy.title}</span>
      <span className="block">{copy.hint}</span>
      <span className="block opacity-90">Connection failed: {message}</span>
    </span>
  );
}

/**
 * Test connection result: an overall verdict, then one card per capability
 * probe (chat, vision/OCR image, embeddings, rerank) with its latency, what
 * it proved and — on failure — the error in plain words.
 */
export function TestResultCard({
  outcome, modelId, formThinking, onApplyThinkingOff,
}: {
  outcome: TestOutcome;
  modelId: string;
  /** The dialog's current thinking setting (the recommendation is hidden once applied). */
  formThinking?: ThinkingMode;
  onApplyThinkingOff?: () => void;
}) {
  if (outcome.kind === 'error') {
    return (
      <div
        role="alert"
        data-testid="endpoint-test-result"
        className={`mt-2 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${PANEL_CLASSES.danger}`}
      >
        <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <FailureHeadline message={outcome.message} kind={null} />
      </div>
    );
  }
  const r = outcome.result;
  const checks = checksOf(r);
  const served = r.served_models ?? [];
  const shown = served.slice(0, MAX_SERVED_SHOWN).join(', ');
  const more = served.length > MAX_SERVED_SHOWN ? ` (+${served.length - MAX_SERVED_SHOWN} more)` : '';
  const multi = checks.length > 1;
  const failureMessage = r.error || r.detail || 'Connection failed';

  return (
    <div
      role={r.ok ? 'status' : 'alert'}
      data-testid="endpoint-test-result"
      className={`mt-2 space-y-2 rounded-lg border px-3 py-2 text-xs ${r.ok ? PANEL_CLASSES.success : PANEL_CLASSES.danger}`}
    >
      {r.ok ? (
        <p className="flex items-center gap-1.5 font-medium">
          <CircleCheck className="h-4 w-4 shrink-0" aria-hidden="true" />
          Connected · {Math.round(r.latency_ms)} ms · {r.probe}
        </p>
      ) : (
        <p className="flex items-start gap-1.5">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <FailureHeadline message={failureMessage} kind={r.error_kind} />
        </p>
      )}
      <ul className="space-y-1.5" aria-label="Capability checks">
        {checks.map((c, i) => (
          <CheckCard
            key={`${c.probe}-${i}`}
            check={c}
            showError={multi}
            formThinking={formThinking}
            onApplyThinkingOff={onApplyThinkingOff}
          />
        ))}
      </ul>
      {r.model_listed === false && (
        <p className={`flex items-start gap-1.5 ${TEXT_TONE.warning}`}>
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 break-words">
            The server does not list {modelId}; it serves: {shown || 'no models'}{more}
          </span>
        </p>
      )}
    </div>
  );
}

function CheckCard({
  check: c, showError, formThinking, onApplyThinkingOff,
}: {
  check: ModelProbeCheck;
  showError: boolean;
  formThinking?: ThinkingMode;
  onApplyThinkingOff?: () => void;
}) {
  const kind = c.ok ? null : probeErrorKind(c.error_kind, c.error ?? '');
  return (
    <li
      data-testid={`probe-check-${c.probe}`}
      data-ok={c.ok ? 'true' : 'false'}
      className="space-y-1 rounded-md border border-current/20 bg-background/60 px-2.5 py-2 text-foreground"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{PROBE_LABEL[c.probe] ?? c.probe}</span>
        {c.ok
          ? <Badge tone="success">Passed</Badge>
          : <Badge tone="danger">{kind ? PROBE_ERROR_COPY[kind].title : 'Failed'}</Badge>}
        <span className="text-muted-foreground" data-testid={`probe-latency-${c.probe}`}>
          {Math.round(c.latency_ms)} ms
        </span>
        {c.capabilities.length > 0 && (
          <span className="text-muted-foreground">covers {c.capabilities.join(', ')}</span>
        )}
      </div>
      {c.detail && <p className="break-words">{c.detail}</p>}
      {!c.ok && showError && c.error && (
        <p className="break-words text-destructive">
          {kind ? `${PROBE_ERROR_COPY[kind].hint} ` : ''}{c.error}
        </p>
      )}
      {c.probe === 'vision' && c.ok && (
        <p data-testid="probe-vision-reply" className="break-words">
          Asked what the test image says (it shows “{c.expected_text ?? 'HELLO'}”): replied “{c.reply}”.{' '}
          {c.text_matched
            ? <span className={TEXT_TONE.success}>Text read correctly.</span>
            : <span className={TEXT_TONE.warning}>The text was not read back — OCR quality may be poor.</span>}
        </p>
      )}
      {c.probe === 'rerank' && c.ok && c.scores && (
        <div data-testid="probe-rerank-scores" className="space-y-0.5">
          <ol className="space-y-0.5">
            {c.scores.map((s) => (
              <li key={s.index} className="flex gap-2">
                <span className="font-mono tabular-nums">{s.score.toFixed(3)}</span>
                <span className="min-w-0 truncate text-muted-foreground" title={s.document}>{s.document}</span>
              </li>
            ))}
          </ol>
          {c.relevant_first === false && (
            <p className={TEXT_TONE.warning}>The relevant document did not rank first.</p>
          )}
        </div>
      )}
      {c.probe === 'embedding' && <EmbeddingFacts check={c} />}
      {c.probe === 'chat' && c.thinking && (
        <ThinkingFacts check={c} formThinking={formThinking} onApplyThinkingOff={onApplyThinkingOff} />
      )}
    </li>
  );
}

function EmbeddingFacts({ check: r }: { check: ModelProbeCheck }) {
  return (
    <>
      {!!r.dimensions && (
        <p data-testid="endpoint-test-dimensions">
          Vector width {r.dimensions}-d
          {r.requested_dimensions ? ` (requested ${r.requested_dimensions})` : ''}
          {r.index_dimension ? ` · vector index ${r.index_dimension}-d (EMBEDDING_DIM)` : ''}
        </p>
      )}
      {r.dimensions_ignored && (
        <p className={`flex items-start gap-1.5 ${TEXT_TONE.warning}`}>
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 break-words">
            The endpoint ignored the requested output dimensions: asked for{' '}
            {r.requested_dimensions}, got {r.dimensions}.
          </span>
        </p>
      )}
      {r.dimension_mismatch && (
        <p
          role="alert"
          data-testid="endpoint-test-dimension-mismatch"
          className={`flex items-start gap-1.5 font-medium ${TEXT_TONE.warning}`}
        >
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 break-words">
            Dimension mismatch: the model returns {r.dimensions}-d vectors but the vector index is{' '}
            {r.index_dimension}-d, so it cannot be the default embedder. Set Output dimensions to{' '}
            {r.index_dimension} if the model supports it, set EMBEDDING_DIM={r.dimensions} and
            re-embed existing collections, or bind it to a {r.dimensions}-d knowledge collection.
          </span>
        </p>
      )}
    </>
  );
}

function ThinkingFacts({
  check, formThinking, onApplyThinkingOff,
}: { check: ModelProbeCheck; formThinking?: ThinkingMode; onApplyThinkingOff?: () => void }) {
  const t = check.thinking!;
  if (!t.thinking_model && !t.recommendation) return null;
  const offRecommended = recommendsThinkingOff(t);
  const applied = offRecommended && formThinking === 'off';
  return (
    <div data-testid="probe-thinking" className="space-y-1">
      {t.thinking_model && (
        <p className="flex items-start gap-1.5">
          <Brain className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            Thinking model detected (tested with thinking <strong>{t.mode}</strong>
            {t.reasoning_tokens ? `, ${t.reasoning_tokens} reasoning tokens` : ''}).
            {t.disabled_works === true && ' It answers with thinking off.'}
            {t.disable_supported === false && ' This endpoint cannot turn thinking off.'}
          </span>
        </p>
      )}
      {t.recommendation && (
        <div
          data-testid="thinking-recommendation"
          className={`flex flex-wrap items-start gap-2 rounded-md border px-2 py-1.5 ${PANEL_CLASSES.warning}`}
        >
          <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 flex-1 break-words">Recommendation: {t.recommendation}</span>
          {offRecommended && onApplyThinkingOff && !applied && (
            <button
              type="button"
              onClick={onApplyThinkingOff}
              className="rounded-md bg-primary px-2 py-1 text-[11px] font-semibold text-primary-foreground hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Apply: thinking off
            </button>
          )}
          {applied && (
            <span role="status" className="font-medium">
              Thinking set to Off — Save to keep it.
            </span>
          )}
        </div>
      )}
    </div>
  );
}
