/** Model Registry dialog helpers (kept out of the component file for Fast Refresh). */
import type { ProbeErrorKind, ThinkingMode, ThinkingProbe } from '@/lib/api/client';

export const MAX_OUTPUT_DIMENSIONS = 8192;
/** Upper bound the backend accepts for thinking_budget_tokens. */
export const MAX_THINKING_BUDGET = 131072;

/** The typed output width: null when empty, NaN when not a valid 1..8192 integer. */
export const parseOutputDimensions = (raw: string): number | null => {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) && n > 0 && n <= MAX_OUTPUT_DIMENSIONS ? n : NaN;
};

/** The typed thinking budget: null when empty, NaN when not a valid 1..131072 integer. */
export const parseThinkingBudget = (raw: string): number | null => {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) && n > 0 && n <= MAX_THINKING_BUDGET ? n : NaN;
};

/** An embedding model whose id does not look like one (e.g. a chat model picked by mistake). */
export const looksLikeChatModel = (modelId: string) => {
  const id = modelId.trim();
  return !!id && !/embed/i.test(id);
};

export const API_KEY_REJECTED_HINT =
  "The provider rejected the API key (or none was sent). Add a key above, or set the provider's key on the server.";

/** A provider failure caused by a missing / invalid credential (HTTP 401/403, or a 400 about the key). */
export const isApiKeyFailure = (message: string) => {
  const status = Number(/HTTP (\d{3})/.exec(message)?.[1] ?? 0);
  if (status === 401 || status === 403) return true;
  return status === 400 && /api[\s_-]?key/i.test(message);
};

// ── Thinking (reasoning-model) control ─────────────────────────────────────────

export const THINKING_OPTIONS: { value: ThinkingMode; label: string; help: string }[] = [
  {
    value: 'auto',
    label: 'Auto',
    help: 'Call the model as it is served; an empty, reasoning-only reply is retried once with thinking off.',
  },
  {
    value: 'off',
    label: 'Off',
    help: 'Always ask for a direct answer with thinking turned off (fastest; needs an endpoint that supports the switch).',
  },
  {
    value: 'on',
    label: 'On',
    help: 'Keep the model reasoning. Add a budget so the reasoning fits before the answer.',
  },
];

/** Whether a Test connection thinking report recommends turning thinking off. */
export const recommendsThinkingOff = (t: ThinkingProbe | undefined | null) =>
  !!t?.recommendation && /"thinking":\s*"off"/.test(t.recommendation);

// ── Provider endpoint hints ────────────────────────────────────────────────────

export interface ProviderHint {
  /** A sensible OpenAI-compatible base URL for the provider (a suggestion, never forced). */
  baseUrl: string;
  /** Placeholder for the model id field. */
  modelId: string;
  /** Whether the provider is usually self-hosted (an endpoint URL is needed). */
  selfHosted?: boolean;
}

const VLLM: ProviderHint = { baseUrl: 'http://<host>:8000/v1', modelId: 'e.g. org/model-name served by vLLM', selfHosted: true };

export const PROVIDER_HINTS: Record<string, ProviderHint> = {
  nvidia: { baseUrl: 'https://integrate.api.nvidia.com/v1', modelId: 'e.g. openai/gpt-oss-20b' },
  openai: { baseUrl: 'https://api.openai.com/v1', modelId: 'e.g. a model id from your OpenAI account' },
  anthropic: { baseUrl: 'https://api.anthropic.com/v1/', modelId: 'e.g. a Claude model id' },
  gemini: { baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', modelId: 'e.g. gemini-embedding-001' },
  google: { baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', modelId: 'e.g. gemini-embedding-001' },
  groq: { baseUrl: 'https://api.groq.com/openai/v1', modelId: 'e.g. a model id Groq serves' },
  xai: { baseUrl: 'https://api.x.ai/v1', modelId: 'e.g. a model id xAI serves' },
  openrouter: { baseUrl: 'https://openrouter.ai/api/v1', modelId: 'e.g. vendor/model' },
  mistral: { baseUrl: 'https://api.mistral.ai/v1', modelId: 'e.g. a Mistral model id' },
  ollama: { baseUrl: 'http://localhost:11434/v1', modelId: 'e.g. a model pulled into Ollama', selfHosted: true },
  onprem: VLLM,
  openai_compatible: VLLM,
  custom: VLLM,
};

export const providerHint = (provider: string): ProviderHint | undefined => PROVIDER_HINTS[provider];

/** A suggested URL still contains a placeholder the operator must replace. */
export const isTemplateUrl = (url: string) => /<[^>]+>/.test(url);

// ── Validation ────────────────────────────────────────────────────────────────

export interface ValidatableForm {
  model_id: string;
  base_url: string;
  capabilities: string[];
  cost_per_1k_input: string;
  cost_per_1k_output: string;
  quality_score: string;
  output_dimensions: string;
  thinking: ThinkingMode;
  thinking_budget_tokens: string;
}

export type FormErrors = Partial<Record<
  'model_id' | 'base_url' | 'capabilities' | 'cost_per_1k_input' | 'cost_per_1k_output'
  | 'quality_score' | 'output_dimensions' | 'thinking_budget_tokens',
  string
>>;

/** An http(s) URL with a host, or the reason it is not one. */
export const baseUrlError = (raw: string): string | undefined => {
  const value = raw.trim();
  if (!value) return undefined;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return 'Enter a full URL starting with http:// or https://';
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return 'The endpoint URL must start with http:// or https://';
  }
  if (!url.hostname) return 'The endpoint URL needs a host';
  return undefined;
};

const costError = (raw: string): string | undefined => {
  const t = raw.trim();
  if (!t) return undefined; // empty = 0
  const n = Number(t);
  if (!Number.isFinite(n)) return 'Enter a number';
  if (n < 0) return 'Cost cannot be negative';
  return undefined;
};

/** Field errors for the Add / Edit dialog; empty object = valid. */
export function validateModelForm(f: ValidatableForm): FormErrors {
  const errors: FormErrors = {};
  const id = f.model_id.trim();
  if (!id) errors.model_id = 'Model ID is required';
  else if (/\s/.test(id)) errors.model_id = 'Model ID cannot contain spaces';
  if (isTemplateUrl(f.base_url)) errors.base_url = 'Replace the <placeholder> in the URL';
  else {
    const url = baseUrlError(f.base_url);
    if (url) errors.base_url = url;
  }
  if (f.capabilities.length === 0) errors.capabilities = 'Choose at least one capability';
  const ci = costError(f.cost_per_1k_input);
  if (ci) errors.cost_per_1k_input = ci;
  const co = costError(f.cost_per_1k_output);
  if (co) errors.cost_per_1k_output = co;
  const q = f.quality_score.trim();
  if (q) {
    const n = Number(q);
    if (!Number.isFinite(n) || n < 0 || n > 1) errors.quality_score = 'Quality must be between 0 and 1';
  }
  if (f.capabilities.includes('embedding') && Number.isNaN(parseOutputDimensions(f.output_dimensions))) {
    errors.output_dimensions = `Output dimensions must be a whole number from 1 to ${MAX_OUTPUT_DIMENSIONS}.`;
  }
  if (
    f.capabilities.includes('text_generation') && f.thinking === 'on'
    && Number.isNaN(parseThinkingBudget(f.thinking_budget_tokens))
  ) {
    errors.thinking_budget_tokens = `Budget must be a whole number of tokens from 1 to ${MAX_THINKING_BUDGET}.`;
  }
  return errors;
}

// ── Probe error mapping ───────────────────────────────────────────────────────

export const PROBE_ERROR_COPY: Record<ProbeErrorKind, { title: string; hint: string }> = {
  auth: {
    title: 'API key rejected',
    hint: API_KEY_REJECTED_HINT,
  },
  model_not_served: {
    title: 'Model not served',
    hint: 'The endpoint answered but does not serve this model id. Check the exact id the server lists.',
  },
  unreachable: {
    title: 'Endpoint unreachable',
    hint: 'Nothing answered at this URL. Check the host, port and path (most servers end in /v1) and that the server is running.',
  },
  unsupported: {
    title: 'Capability not supported',
    hint: 'The endpoint or model does not support this capability. Untick it, or point it at a model that does.',
  },
  refused: {
    title: 'URL not allowed',
    hint: "The server's egress policy refuses this address.",
  },
  thinking_budget: {
    title: 'Ran out of budget while thinking',
    hint: 'This is a thinking model: set Thinking to Off, or On with a larger budget.',
  },
  invalid_response: {
    title: 'Unexpected response',
    hint: 'The endpoint answered, but not with what this capability needs.',
  },
  http_error: {
    title: 'Request failed',
    hint: 'The endpoint returned an error.',
  },
};

/** The error kind for a probe failure; inferred from the text on older backends. */
export function probeErrorKind(kind: ProbeErrorKind | null | undefined, message: string): ProbeErrorKind {
  if (kind && kind in PROBE_ERROR_COPY) return kind;
  if (isApiKeyFailure(message)) return 'auth';
  if (/^thinking model:/.test(message)) return 'thinking_budget';
  const status = Number(/HTTP (\d{3})/.exec(message)?.[1] ?? 0);
  if (status && /model[^.]{0,80}(not found|does not exist|unknown)/i.test(message)) return 'model_not_served';
  if (status === 404 || status === 405 || status === 501) return 'unsupported';
  if (status) return 'http_error';
  if (/not allowed|refused or invalid|egress/i.test(message)) return 'refused';
  return 'unreachable';
}

export const PROBE_LABEL: Record<string, string> = {
  chat: 'Reasoning (chat)',
  vision: 'Vision / OCR (image)',
  embedding: 'Embeddings',
  rerank: 'Rerank',
  speech_to_text: 'Speech-to-text (audio)',
  text_to_speech: 'Text-to-speech (audio)',
};

