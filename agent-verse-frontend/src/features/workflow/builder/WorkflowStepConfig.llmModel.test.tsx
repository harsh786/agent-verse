/**
 * The LLM Prompt node's Model dropdown lists the tenant's configured Model
 * Registry text-generation models (GET /models/configured) — display name +
 * provider, "Default (registry order)" first. It used to be a hard-coded list
 * (GPT-4o, GPT-4o Mini, Claude 3.5 Sonnet, Claude 3.5 Haiku, Gemini 1.5 Pro).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { Node } from '@xyflow/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { ConfiguredModel } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';
import { WorkflowStepConfig } from './WorkflowStepConfig';
import { DEFAULT_MODEL_OPTION, llmModelOptions } from './llmModelOptions';

function model(over: Partial<ConfiguredModel>): ConfiguredModel {
  return {
    key: `${over.provider ?? 'openai_compatible'}/${over.model_id ?? 'm'}`,
    provider: 'openai_compatible',
    model_id: 'm',
    display_name: 'm',
    capabilities: ['text_generation'],
    cost_per_1k_input: 0,
    cost_per_1k_output: 0,
    supports_tools: true,
    supports_vision: false,
    supports_structured_output: true,
    quality_score: 0.7,
    is_available: true,
    provider_ready: true,
    servable: true,
    source: 'override',
    rank: 1,
    base_url: 'http://10.0.0.5:8000/v1',
    ...over,
  };
}

const QWEN = model({ model_id: 'qwen3-32b', display_name: 'Qwen 3 32B', rank: 1 });
const GROQ = model({ provider: 'groq', model_id: 'llama-3.3-70b', display_name: 'llama-3.3-70b', base_url: null, rank: 2 });
const DEAD = model({ provider: 'openai', model_id: 'gpt-4o', display_name: 'gpt-4o', source: 'env', servable: false, base_url: null, rank: 3 });
const EMBED = model({ model_id: 'bge-m3', capabilities: ['embedding'] });

function mockBackend() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const json = (b: unknown) =>
      new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (url.endsWith('/models/configured')) {
      return json({
        total: 4,
        capabilities: [
          { capability: 'text_generation', status: 'ready', ready_count: 2, selected_model_id: 'qwen3-32b', fallback_model_ids: ['llama-3.3-70b'], order_mode: 'preference', preference: [], models: [QWEN, GROQ, DEAD] },
          { capability: 'embedding', status: 'ready', ready_count: 1, selected_model_id: 'bge-m3', fallback_model_ids: [], order_mode: 'cost', preference: [], models: [EMBED] },
        ],
      });
    }
    return json([]);
  });
}

function renderLlm(data: Record<string, unknown> = {}) {
  const onUpdate = vi.fn();
  const node = { id: 'step-1', type: 'llm', position: { x: 0, y: 0 }, data: { stepType: 'llm', ...data } } as unknown as Node;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <WorkflowStepConfig node={node} onUpdate={onUpdate} onClose={() => undefined} />
    </QueryClientProvider>,
  );
  return onUpdate;
}

beforeEach(() => {
  useAuthStore.setState({ apiKey: 'k', tenantId: 't', plan: 'free', isAuthenticated: true });
});
afterEach(() => vi.restoreAllMocks());

describe('LLM node — Model dropdown from the Model Registry', () => {
  test('lists Default first, then the configured text models with display name + provider', async () => {
    mockBackend();
    renderLlm();
    const select = screen.getByLabelText('Model') as HTMLSelectElement;
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(4));
    const labels = within(select).getAllByRole('option').map((o) => o.textContent);
    expect(labels[0]).toBe(DEFAULT_MODEL_OPTION);
    expect(labels[1]).toBe('Qwen 3 32B (qwen3-32b) · openai_compatible');
    expect(labels[2]).toBe('llama-3.3-70b · groq');
    expect(labels[3]).toMatch(/^gpt-4o · openai — not usable/);
    // An embedding-only model is not a choice for an LLM prompt.
    expect(labels.join('|')).not.toContain('bge-m3');
    // None of the old hard-coded models.
    expect(labels.join('|')).not.toMatch(/Claude 3\.5|Gemini 1\.5|GPT-4o Mini/);
    expect(select).toHaveValue('');
    expect(within(select).getByRole('option', { name: /gpt-4o · openai/ })).toBeDisabled();
  });

  test('picking a registry model saves its model id', async () => {
    mockBackend();
    const onUpdate = renderLlm();
    const select = screen.getByLabelText('Model');
    await waitFor(() => expect(within(select).getAllByRole('option').length).toBeGreaterThan(1));
    fireEvent.change(select, { target: { value: 'llama-3.3-70b' } });
    expect(onUpdate).toHaveBeenCalledWith({ model: 'llama-3.3-70b' });
  });

  test('a saved model that is not configured is shown and flagged', async () => {
    mockBackend();
    renderLlm({ model: 'claude-3-5-sonnet-20241022' });
    expect(await screen.findByRole('alert')).toHaveTextContent(
      /claude-3-5-sonnet-20241022 is not configured in the Model Registry/,
    );
    expect(screen.getByLabelText('Model')).toHaveValue('claude-3-5-sonnet-20241022');
  });
});

describe('llmModelOptions', () => {
  test('dedupes a model served by two providers and keeps the current one enabled', () => {
    const twin = model({ provider: 'onprem', model_id: 'qwen3-32b', key: 'onprem/qwen3-32b' });
    const opts = llmModelOptions([QWEN, twin, DEAD], 'gpt-4o');
    expect(opts.map((o) => o.value)).toEqual(['', 'qwen3-32b', 'gpt-4o']);
    expect(opts.find((o) => o.value === 'gpt-4o')?.disabled).toBe(false);
  });
});
