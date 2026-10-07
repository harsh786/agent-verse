import { useId, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Check, Info, KeyRound, Loader2, Plug, TriangleAlert, XCircle } from 'lucide-react';
import { modelsApi } from '@/lib/api/client';
import { Badge } from './Badge';
import { PANEL_CLASSES, TEXT_TONE } from './badgeStyles';
import { CAPABILITY_CHIPS, PROVIDERS, type FormState } from './modelForm';
import { Modal } from './Modal';
import { TestResultCard, type TestOutcome } from './TestResultCard';
import {
  MAX_OUTPUT_DIMENSIONS,
  MAX_THINKING_BUDGET,
  THINKING_OPTIONS,
  baseUrlError,
  isTemplateUrl,
  looksLikeChatModel,
  parseOutputDimensions,
  parseThinkingBudget,
  providerHint,
  validateModelForm,
  type FormErrors,
} from './modelFormHelpers';

/** The fields a connection test depends on — a result is only shown while they are unchanged. */
const testSignature = (f: FormState) =>
  [f.provider, f.model_id.trim(), f.base_url.trim(), f.api_key, f.output_dimensions.trim(),
    [...f.capabilities].sort().join(',')].join('\n');

const INPUT_CLS =
  'w-full rounded-lg border bg-background px-3 py-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring aria-[invalid=true]:border-destructive';
const LABEL_CLS = 'mb-1 block text-xs font-medium text-muted-foreground';

const FIELD_LABELS: Record<keyof FormErrors, string> = {
  model_id: 'Model ID',
  base_url: 'Endpoint URL',
  capabilities: 'Capabilities',
  cost_per_1k_input: 'Cost / 1k input',
  cost_per_1k_output: 'Cost / 1k output',
  quality_score: 'Quality',
  output_dimensions: 'Output dimensions',
  thinking_budget_tokens: 'Thinking budget',
};

interface Props {
  initial: FormState;
  editingKey: string | null;
  adminKey?: string;
  canModify: boolean;
  denyTitle: string;
  /** The vector index width (EMBEDDING_DIM), as reported on the embedding rows. */
  indexDimension?: number | null;
  onClose: () => void;
  onSaved: (modelId: string) => void;
}

/**
 * Add / Edit a registry model: identity, endpoint + write-only key with a
 * capability-aware Test connection, costs, capabilities, embedding width and
 * the thinking control for reasoning models. Validates before saving and
 * shows each error next to its field.
 */
export function ModelFormDialog({
  initial, editingKey, adminKey, canModify, denyTitle, indexDimension, onClose, onSaved,
}: Props) {
  const uid = useId();
  const id = (name: string) => `mr-${name}-${uid}`;
  const [form, setForm] = useState<FormState>(initial);
  const [formError, setFormError] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [testOutcome, setTestOutcome] = useState<TestOutcome | null>(null);

  const errors = submitted ? validateModelForm(form) : {};
  const embeddingSelected = form.capabilities.includes('embedding');
  const textSelected = form.capabilities.includes('text_generation');
  const outputDims = parseOutputDimensions(form.output_dimensions);
  const hint = providerHint(form.provider);

  /** Update a form field; a change to what the connection test probed clears its result. */
  const setField = <K extends keyof FormState>(field: K, value: FormState[K]) => {
    const next = { ...form, [field]: value };
    if (testSignature(next) !== testSignature(form)) setTestOutcome(null);
    setForm(next);
  };

  const toggleCap = (cap: string) =>
    setField(
      'capabilities',
      form.capabilities.includes(cap)
        ? form.capabilities.filter((c) => c !== cap)
        : [...form.capabilities, cap],
    );

  const thinkingFields = (f: FormState) =>
    f.capabilities.includes('text_generation')
      ? {
          thinking: f.thinking,
          ...(f.thinking === 'on'
            ? { thinking_budget_tokens: parseThinkingBudget(f.thinking_budget_tokens) }
            : {}),
        }
      : {};

  const upsert = useMutation({
    mutationFn: (f: FormState) => {
      const baseUrl = f.base_url.trim();
      const apiKeyTyped = f.api_key.trim();
      return modelsApi.upsertConfigured({
        model_id: f.model_id.trim(),
        display_name: f.display_name.trim() || undefined,
        provider: f.provider || 'custom',
        capabilities: f.capabilities,
        cost_per_1k_input: Number(f.cost_per_1k_input) || 0,
        cost_per_1k_output: Number(f.cost_per_1k_output) || 0,
        quality_score: Math.min(1, Math.max(0, Number(f.quality_score) || 0)),
        supports_tools: f.supports_tools,
        supports_vision: f.supports_vision || f.capabilities.includes('vision'),
        ...(f.supports_structured_output !== undefined
          ? { supports_structured_output: f.supports_structured_output }
          : {}),
        ...(baseUrl ? { base_url: baseUrl } : {}),
        ...(apiKeyTyped
          ? { api_key: apiKeyTyped }
          : f.clear_api_key ? { clear_api_key: true } : {}),
        ...(f.capabilities.includes('embedding')
          ? { output_dimensions: parseOutputDimensions(f.output_dimensions) }
          : {}),
        ...thinkingFields(f),
      }, adminKey);
    },
    onSuccess: (_r, f) => onSaved(f.model_id.trim()),
    onError: (e: Error) => setFormError(e.message || 'Failed to save model'),
  });

  const testConn = useMutation({
    mutationFn: ({ f }: { f: FormState; sig: string }) =>
      modelsApi.testEndpoint({
        provider: f.provider || 'custom',
        model_id: f.model_id.trim(),
        base_url: f.base_url.trim(),
        capabilities: f.capabilities,
        ...(f.api_key.trim() ? { api_key: f.api_key.trim() } : {}),
        ...(f.capabilities.includes('embedding')
          ? { output_dimensions: parseOutputDimensions(f.output_dimensions) }
          : {}),
        ...thinkingFields(f),
      }, adminKey),
    onSuccess: (result, { sig }) => setTestOutcome({ sig, kind: 'result', result }),
    onError: (e: Error, { sig }) =>
      setTestOutcome({ sig, kind: 'error', message: e.message || 'Connection test failed' }),
  });

  const save = () => {
    setSubmitted(true);
    const errs = validateModelForm(form);
    const messages = (Object.keys(errs) as (keyof FormErrors)[]).map(
      (k) => `${FIELD_LABELS[k]}: ${errs[k]}`,
    );
    if (messages.length > 0) {
      setFormError(`Fix the highlighted fields — ${messages.join(' · ')}`);
      return;
    }
    setFormError('');
    upsert.mutate(form);
  };

  const describedBy = (...ids: (string | false | undefined)[]) =>
    ids.filter(Boolean).join(' ') || undefined;
  const fieldError = (field: keyof FormErrors) =>
    errors[field] ? (
      <p id={id(`${field}-error`)} className="mt-1 text-xs text-destructive">{errors[field]}</p>
    ) : null;

  const urlProblem = baseUrlError(form.base_url) || (isTemplateUrl(form.base_url) ? 'placeholder' : undefined);
  const testDisabledReason = !canModify
    ? denyTitle
    : !form.model_id.trim() ? 'Enter a model ID first'
      : !form.base_url.trim() ? 'Enter the endpoint URL first'
        : urlProblem ? 'Fix the endpoint URL first'
          : form.capabilities.length === 0 ? 'Choose at least one capability'
            : undefined;

  return (
    <Modal labelledBy={id('title')} describedBy={id('desc')} onClose={onClose} widthClass="max-w-2xl">
      <div className="border-b border-border px-6 py-4">
        <h3 id={id('title')} className="text-lg font-semibold">
          {editingKey ? 'Edit model' : 'Add / override a model'}
        </h3>
        <p id={id('desc')} className="mt-1 text-xs text-muted-foreground">
          Register a model your provider can serve. Set its cost so the cheapest one is used
          first when no preference order is saved for its capability.
        </p>
      </div>
      <form
        noValidate
        onSubmit={(e) => { e.preventDefault(); save(); }}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor={id('model-id')} className={LABEL_CLS}>Model ID *</label>
              <input
                id={id('model-id')}
                value={form.model_id}
                onChange={(e) => setField('model_id', e.target.value)}
                placeholder={hint?.modelId ?? 'The exact model id your server serves'}
                aria-invalid={!!errors.model_id}
                aria-required="true"
                aria-describedby={describedBy(errors.model_id && id('model_id-error'))}
                autoComplete="off"
                spellCheck={false}
                className={`${INPUT_CLS} border-input`}
              />
              {fieldError('model_id')}
            </div>
            <div>
              <label htmlFor={id('provider')} className={LABEL_CLS}>Provider</label>
              <select
                id={id('provider')}
                value={form.provider}
                onChange={(e) => setField('provider', e.target.value)}
                className={`${INPUT_CLS} border-input`}
              >
                {/* Keep an unknown provider from an existing row selectable when editing. */}
                {!(PROVIDERS as readonly string[]).includes(form.provider) && form.provider && (
                  <option value={form.provider}>{form.provider}</option>
                )}
                {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </div>
          </div>
          {editingKey && (
            <p className="-mt-2 text-xs text-muted-foreground">
              Model ID and provider identify the entry — changing either creates a new entry
              instead of updating <span className="font-mono">{editingKey}</span>.
            </p>
          )}
          <div>
            <label htmlFor={id('base-url')} className={LABEL_CLS}>Endpoint URL (optional)</label>
            <input
              id={id('base-url')}
              type="url"
              inputMode="url"
              value={form.base_url}
              onChange={(e) => setField('base_url', e.target.value)}
              placeholder={hint?.baseUrl ?? 'https://host/v1'}
              aria-invalid={!!errors.base_url}
              aria-describedby={describedBy(id('base-url-help'), errors.base_url && id('base_url-error'))}
              spellCheck={false}
              className={`${INPUT_CLS} border-input`}
            />
            {fieldError('base_url')}
            <p id={id('base-url-help')} className="mt-1 text-xs text-muted-foreground">
              Base URL of an OpenAI-compatible server (vLLM, Ollama /v1, on-prem). Leave empty to
              use the provider's configured API.
              {hint && (
                <> Typical for {form.provider}: <span className="font-mono">{hint.baseUrl}</span>.</>
              )}
            </p>
            {hint && !form.base_url.trim() && !isTemplateUrl(hint.baseUrl) && (
              <button
                type="button"
                onClick={() => setField('base_url', hint.baseUrl)}
                className="mt-1 text-xs font-medium text-primary hover:underline focus:outline-none focus:ring-2 focus:ring-ring"
              >
                Use {hint.baseUrl}
              </button>
            )}
          </div>
          <div>
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
              <label htmlFor={id('api-key')} className="block text-xs font-medium text-muted-foreground">
                API key
              </label>
              {form.has_api_key && !form.clear_api_key && (
                <span className="flex items-center gap-2">
                  <Badge tone="success" testId="api-key-saved"><KeyRound className="h-3 w-3" aria-hidden="true" /> Key saved</Badge>
                  <button
                    type="button"
                    onClick={() => setField('clear_api_key', true)}
                    className="text-xs text-destructive hover:underline"
                  >
                    Remove key
                  </button>
                </span>
              )}
              {form.has_api_key && form.clear_api_key && (
                <span className={`flex items-center gap-2 text-xs ${TEXT_TONE.warning}`}>
                  The saved key is removed on Save.
                  <button
                    type="button"
                    onClick={() => setField('clear_api_key', false)}
                    className="text-primary hover:underline"
                  >
                    Undo
                  </button>
                </span>
              )}
            </div>
            <input
              id={id('api-key')}
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              value={form.api_key}
              onChange={(e) => setField('api_key', e.target.value)}
              placeholder="Leave empty to keep the saved key / use the provider's configured key"
              aria-describedby={id('api-key-help')}
              className={`${INPUT_CLS} border-input`}
            />
            <p id={id('api-key-help')} className="mt-1 text-xs text-muted-foreground">
              Sent as the Bearer key to this model's endpoint. Stored encrypted on the server and
              never shown again.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => testConn.mutate({ f: form, sig: testSignature(form) })}
                disabled={!!testDisabledReason || testConn.isPending}
                title={testDisabledReason ?? 'Probe every selected capability on this endpoint'}
                aria-describedby={id('test-help')}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              >
                {testConn.isPending
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                  : <Plug className="h-3.5 w-3.5" aria-hidden="true" />}
                Test connection
              </button>
              <span id={id('test-help')} className="text-xs text-muted-foreground">
                {testDisabledReason && canModify
                  ? testDisabledReason
                  : 'Makes one real call per selected capability (chat, image, embedding, rerank).'}
              </span>
            </div>
            {testOutcome && testOutcome.sig === testSignature(form) && (
              <TestResultCard
                outcome={testOutcome}
                modelId={form.model_id.trim()}
                formThinking={form.thinking}
                onApplyThinkingOff={textSelected ? () => setField('thinking', 'off') : undefined}
              />
            )}
          </div>
          <div>
            <label htmlFor={id('display-name')} className={LABEL_CLS}>Display name</label>
            <input
              id={id('display-name')}
              value={form.display_name}
              onChange={(e) => setField('display_name', e.target.value)}
              placeholder="Optional"
              className={`${INPUT_CLS} border-input`}
            />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {([
              ['cost_per_1k_input', 'cost-in', 'Cost / 1k input ($)', '0.00001', undefined],
              ['cost_per_1k_output', 'cost-out', 'Cost / 1k output ($)', '0.00001', undefined],
              ['quality_score', 'quality', 'Quality (0–1)', '0.05', '1'],
            ] as const).map(([field, name, label, step, max]) => (
              <div key={field}>
                <label htmlFor={id(name)} className={LABEL_CLS}>{label}</label>
                <input
                  id={id(name)}
                  type="number" step={step} min="0" max={max}
                  value={form[field]}
                  onChange={(e) => setField(field, e.target.value)}
                  aria-invalid={!!errors[field]}
                  aria-describedby={describedBy(errors[field] && id(`${field}-error`))}
                  className={`${INPUT_CLS} border-input`}
                />
                {fieldError(field)}
              </div>
            ))}
          </div>
          <fieldset aria-describedby={describedBy(errors.capabilities && id('capabilities-error'))}>
            <legend className={LABEL_CLS}>Capabilities *</legend>
            <div className="flex flex-wrap gap-2">
              {CAPABILITY_CHIPS.map((c) => (
                <button
                  key={c.key}
                  type="button"
                  onClick={() => toggleCap(c.key)}
                  aria-pressed={form.capabilities.includes(c.key)}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-ring ${
                    form.capabilities.includes(c.key)
                      ? 'bg-primary text-primary-foreground'
                      : 'border border-border bg-background text-muted-foreground hover:bg-muted'
                  }`}
                >
                  {c.label}
                </button>
              ))}
            </div>
            {fieldError('capabilities')}
            {embeddingSelected && looksLikeChatModel(form.model_id) && (
              <p data-testid="chat-model-hint" className={`mt-2 flex items-start gap-1.5 text-xs ${TEXT_TONE.warning}`}>
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span>
                  This looks like a chat model; embedding models are usually named
                  …-embedding-… (e.g. <span className="font-mono">gemini-embedding-001</span>,{' '}
                  <span className="font-mono">text-embedding-3-small</span>).
                </span>
              </p>
            )}
          </fieldset>
          {textSelected && (
            <fieldset data-testid="thinking-control" aria-describedby={id('thinking-help')}>
              <legend className={LABEL_CLS}>Thinking (reasoning models)</legend>
              <div role="radiogroup" aria-label="Thinking" className="inline-flex rounded-lg border border-border p-0.5">
                {THINKING_OPTIONS.map((o) => (
                  <label
                    key={o.value}
                    className={`cursor-pointer rounded-md px-3 py-1 text-xs font-medium focus-within:ring-2 focus-within:ring-ring ${
                      form.thinking === o.value ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'
                    }`}
                  >
                    <input
                      type="radio"
                      name={id('thinking')}
                      value={o.value}
                      checked={form.thinking === o.value}
                      onChange={() => setField('thinking', o.value)}
                      className="sr-only"
                    />
                    {o.label}
                  </label>
                ))}
              </div>
              <p id={id('thinking-help')} className="mt-1 text-xs text-muted-foreground">
                {THINKING_OPTIONS.find((o) => o.value === form.thinking)?.help}{' '}
                Test connection tells you whether the model thinks and which setting answers best.
              </p>
              {form.thinking === 'on' && (
                <div className="mt-2 max-w-xs">
                  <label htmlFor={id('thinking-budget')} className={LABEL_CLS}>Thinking budget (tokens, optional)</label>
                  <input
                    id={id('thinking-budget')}
                    type="number" min="1" max={MAX_THINKING_BUDGET} step="1"
                    value={form.thinking_budget_tokens}
                    onChange={(e) => setField('thinking_budget_tokens', e.target.value)}
                    placeholder="e.g. 2048"
                    aria-invalid={!!errors.thinking_budget_tokens}
                    aria-describedby={describedBy(id('thinking-budget-help'), errors.thinking_budget_tokens && id('thinking_budget_tokens-error'))}
                    className={`${INPUT_CLS} border-input`}
                  />
                  {fieldError('thinking_budget_tokens')}
                  <p id={id('thinking-budget-help')} className="mt-1 text-xs text-muted-foreground">
                    Extra tokens added to each call's budget so the reasoning fits before the answer.
                  </p>
                </div>
              )}
            </fieldset>
          )}
          {embeddingSelected && (
            <div>
              <label htmlFor={id('output-dims')} className={LABEL_CLS}>Output dimensions (optional)</label>
              <input
                id={id('output-dims')}
                type="number"
                min="1"
                max={MAX_OUTPUT_DIMENSIONS}
                step="1"
                value={form.output_dimensions}
                onChange={(e) => setField('output_dimensions', e.target.value)}
                placeholder="Native width"
                aria-invalid={!!errors.output_dimensions || Number.isNaN(outputDims)}
                aria-describedby={describedBy(id('output-dims-help'), Number.isNaN(outputDims) && id('output_dimensions-error'))}
                className={`${INPUT_CLS} border-input`}
              />
              <p id={id('output-dims-help')} className="mt-1 text-xs text-muted-foreground">
                Vector width to request (sent as <span className="font-mono">dimensions</span>) from
                models that can shorten their vectors, e.g. gemini-embedding-001 (768 / 1536 / 3072)
                or text-embedding-3-*. It must equal the vector index width
                {indexDimension ? <> (<strong>{indexDimension}</strong>, EMBEDDING_DIM)</> : ' (EMBEDDING_DIM)'}.
              </p>
              {Number.isNaN(outputDims) && (
                <p id={id('output_dimensions-error')} className="mt-1 text-xs text-destructive">
                  Enter a whole number from 1 to {MAX_OUTPUT_DIMENSIONS}.
                </p>
              )}
              {!!outputDims && !!indexDimension && outputDims !== indexDimension && (
                <p className={`mt-1 flex items-start gap-1.5 text-xs ${TEXT_TONE.warning}`}>
                  <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  {outputDims}-d does not match the {indexDimension}-d vector index: the model
                  cannot be the default embedder (a knowledge collection of that width can still
                  use it).
                </p>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.supports_tools}
                onChange={(e) => setField('supports_tools', e.target.checked)}
                className="h-4 w-4 rounded border-input accent-primary" />
              Supports tools
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.supports_vision}
                onChange={(e) => setField('supports_vision', e.target.checked)}
                className="h-4 w-4 rounded border-input accent-primary" />
              Supports vision
            </label>
          </div>
          {formError && (
            <div role="alert" className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${PANEL_CLASSES.danger}`}>
              <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" /> {formError}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-3 border-t border-border px-6 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border px-4 py-2 text-sm hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={upsert.isPending}
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
          >
            {upsert.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {upsert.isPending ? 'Saving…' : (<><Check className="h-4 w-4" aria-hidden="true" /> Save</>)}
          </button>
        </div>
      </form>
    </Modal>
  );
}
