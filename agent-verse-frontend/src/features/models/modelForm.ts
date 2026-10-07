/** The Add / Edit dialog's form model (Fast Refresh: no components here). */
import type { ConfiguredModel, ThinkingMode } from '@/lib/api/client';

export const CAPABILITY_CHIPS = [
  { key: 'text_generation', label: 'Reasoning' },
  { key: 'embedding', label: 'Embeddings' },
  { key: 'vision', label: 'Vision' },
  { key: 'ocr', label: 'OCR' },
  { key: 'rerank', label: 'Reranker' },
] as const;

/** Providers the backend accepts on POST /models/configured. */
export const PROVIDERS = [
  'nvidia', 'anthropic', 'openai', 'openai_compatible', 'azure_openai', 'gemini', 'google',
  'voyage', 'groq', 'xai', 'ollama', 'onprem', 'openrouter', 'bedrock', 'vertex', 'mistral',
  'cohere', 'custom',
] as const;

export interface FormState {
  model_id: string;
  display_name: string;
  provider: string;
  /** Base URL of an OpenAI-compatible server; empty = the provider's configured API. */
  base_url: string;
  capabilities: string[];
  cost_per_1k_input: string;
  cost_per_1k_output: string;
  quality_score: string;
  supports_tools: boolean;
  supports_vision: boolean;
  /** Not editable in the form; carried over when editing so an upsert keeps it. */
  supports_structured_output?: boolean;
  /**
   * Write-only endpoint credential typed by the operator. Lives in component
   * state only (never localStorage), is sent on Save / Test connection and is
   * cleared when the dialog closes. The saved key is never shown.
   */
  api_key: string;
  /** The entry already has a saved (vault-encrypted) key — display only. */
  has_api_key: boolean;
  /** Remove the saved key on Save. */
  clear_api_key: boolean;
  /** Embedding models: the vector width to request; empty = native width. */
  output_dimensions: string;
  /** Reasoning models: thinking control. */
  thinking: ThinkingMode;
  /** Reasoning tokens added with thinking "on"; empty = none. */
  thinking_budget_tokens: string;
}

export const EMPTY_FORM: FormState = {
  model_id: '',
  display_name: '',
  provider: 'nvidia',
  base_url: '',
  capabilities: ['text_generation'],
  cost_per_1k_input: '0',
  cost_per_1k_output: '0',
  quality_score: '0.5',
  supports_tools: true,
  supports_vision: false,
  api_key: '',
  has_api_key: false,
  clear_api_key: false,
  output_dimensions: '',
  thinking: 'auto',
  thinking_budget_tokens: '',
};

export const formFromModel = (m: ConfiguredModel): FormState => ({
  model_id: m.model_id,
  display_name: m.display_name && m.display_name !== m.model_id ? m.display_name : '',
  provider: m.provider,
  base_url: m.base_url ?? '',
  capabilities: [...m.capabilities],
  cost_per_1k_input: String(m.cost_per_1k_input ?? 0),
  cost_per_1k_output: String(m.cost_per_1k_output ?? 0),
  quality_score: String(m.quality_score ?? 0.5),
  supports_tools: !!m.supports_tools,
  supports_vision: !!m.supports_vision,
  supports_structured_output: m.supports_structured_output,
  api_key: '',
  has_api_key: !!m.has_api_key,
  clear_api_key: false,
  output_dimensions: m.output_dimensions ? String(m.output_dimensions) : '',
  thinking: m.thinking ?? 'auto',
  thinking_budget_tokens: m.thinking_budget_tokens ? String(m.thinking_budget_tokens) : '',
});
