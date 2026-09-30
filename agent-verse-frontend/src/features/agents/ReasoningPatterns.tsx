// Per-agent reasoning-pattern opt-ins. Mirrors the backend's
// app.agent.pattern_flags.AGENT_PATTERN_FLAG_KEYS: each flag compiles an extra
// node into the agent's graph (and costs extra LLM calls when it runs).

export const PATTERN_FLAGS = [
  { key: 'enable_cot', label: 'Chain-of-thought', hint: 'Reason step by step before acting' },
  { key: 'enable_reflection', label: 'Reflection', hint: 'Critique the result and retry once' },
  { key: 'enable_goal_tree', label: 'Goal tree', hint: 'Decompose large goals into sub-goals' },
  { key: 'enable_self_refine', label: 'Self-refine', hint: 'Iteratively improve the answer' },
  { key: 'enable_self_consistency', label: 'Self-consistency', hint: 'Sample several answers, keep the agreed one' },
  { key: 'enable_tree_of_thoughts', label: 'Tree of thoughts', hint: 'Explore alternative plans' },
  { key: 'enable_peer_review', label: 'Peer review', hint: 'A second reviewer checks the output' },
  { key: 'enable_supervisor', label: 'Supervisor', hint: 'Split work across sub-agents' },
  { key: 'enable_debate', label: 'Debate', hint: 'Agents argue proposals, then vote' },
] as const;

export type PatternFlagKey = (typeof PATTERN_FLAGS)[number]['key'];
export type PatternFlags = Record<PatternFlagKey, boolean>;

export function emptyPatternFlags(): PatternFlags {
  return Object.fromEntries(PATTERN_FLAGS.map((f) => [f.key, false])) as PatternFlags;
}

/** Read the flags off an agent record (the pattern_flags map, then flat keys). */
export function patternFlagsFromAgent(agent: unknown): PatternFlags {
  const flags = emptyPatternFlags();
  if (!agent || typeof agent !== 'object') return flags;
  const rec = agent as Record<string, unknown>;
  const nested = rec.pattern_flags;
  for (const { key } of PATTERN_FLAGS) {
    if (nested && typeof nested === 'object' && key in (nested as Record<string, unknown>)) {
      flags[key] = (nested as Record<string, unknown>)[key] === true;
    }
    if (key in rec) flags[key] = rec[key] === true;
  }
  return flags;
}

export function ReasoningPatternsFieldset({
  value,
  onChange,
  className = '',
}: {
  value: PatternFlags;
  onChange: (next: PatternFlags) => void;
  className?: string;
}) {
  return (
    <fieldset className={className}>
      <legend className="block text-sm font-medium mb-1">Reasoning patterns</legend>
      <p className="text-xs opacity-60 mb-2">
        Extra reasoning steps compiled into this agent's runs. Each one adds LLM calls.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
        {PATTERN_FLAGS.map((f) => (
          <label key={f.key} className="flex items-start gap-2 text-sm cursor-pointer" title={f.hint}>
            <input
              type="checkbox"
              checked={value[f.key]}
              onChange={(e) => onChange({ ...value, [f.key]: e.target.checked })}
              className="mt-0.5 h-4 w-4 rounded"
            />
            <span>
              {f.label}
              <span className="block text-[11px] opacity-50">{f.hint}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function ReasoningPatternsSummary({ agent }: { agent: unknown }) {
  const flags = patternFlagsFromAgent(agent);
  const enabled = PATTERN_FLAGS.filter((f) => flags[f.key]);
  return (
    <p className="text-xs text-muted-foreground" data-testid="reasoning-patterns">
      <span className="font-medium text-foreground">Reasoning patterns: </span>
      {enabled.length > 0 ? enabled.map((f) => f.label).join(', ') : 'none enabled'}
    </p>
  );
}
