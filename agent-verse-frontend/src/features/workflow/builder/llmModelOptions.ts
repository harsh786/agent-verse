import type { ConfiguredModel } from '@/lib/api/client';

/** "Default (registry order)": no model saved, the Model Registry's order picks. */
export const DEFAULT_MODEL_OPTION = 'Default (registry order)';

const modelUsable = (m: ConfiguredModel) => m.provider_ready && m.servable !== false;

/**
 * The LLM Prompt node's Model options: the tenant's configured text-generation
 * models from the Model Registry (GET /models/configured), in execution order,
 * after "Default (registry order)". A model that cannot serve right now is listed
 * but disabled; a saved model that is not configured stays visible (flagged) so
 * the step never silently shows another model than the one it will request.
 */
export function llmModelOptions(
  models: ConfiguredModel[],
  current: string,
): { label: string; value: string; disabled?: boolean }[] {
  const options: { label: string; value: string; disabled?: boolean }[] = [
    { label: DEFAULT_MODEL_OPTION, value: '' },
  ];
  const seen = new Set<string>();
  for (const m of models) {
    if (seen.has(m.model_id)) continue;
    seen.add(m.model_id);
    const name = m.display_name && m.display_name !== m.model_id
      ? `${m.display_name} (${m.model_id})`
      : m.model_id;
    const usable = modelUsable(m);
    options.push({
      label: `${name} · ${m.provider}${usable ? '' : ' — not usable (no key or endpoint)'}`,
      value: m.model_id,
      disabled: !usable && m.model_id !== current,
    });
  }
  if (current && !seen.has(current)) {
    options.push({ label: `${current} — not configured in the Model Registry`, value: current });
  }
  return options;
}
