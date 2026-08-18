// agent-verse-frontend/src/features/observability/RuntimeDecisionPanel.tsx

interface DecisionEntry {
  selector: string;
  dimension: string;
  selected: string;
  reason: string;
  latency_ms?: number;
}

interface RuntimeDecisionPanelProps {
  goalId: string;
  decisions?: DecisionEntry[];
  complexity?: string;
  risk?: string;
  patternsActive?: Record<string, string[]>;
  assemblyLatencyMs?: number;
  ragStrategy?: string;
  modelTier?: string;
  guardrailBundle?: string;
  overallScore?: number | null;
}

export function RuntimeDecisionPanel({
  goalId,
  decisions = [],
  complexity,
  risk,
  patternsActive,
  assemblyLatencyMs,
  ragStrategy,
  modelTier,
  guardrailBundle,
  overallScore,
}: RuntimeDecisionPanelProps) {
  return (
    <div className="runtime-decision-panel border rounded-lg p-4 bg-[#0A0F1A] text-sm" data-goal-id={goalId}>
      <h3 className="font-semibold text-[#F0F6FF] mb-3">
        Runtime Orchestration Decisions
      </h3>

      {/* Summary row */}
      <div className="flex flex-wrap gap-2 mb-4">
        {complexity && (
          <span className="px-2 py-1 bg-blue-100 text-blue-800 rounded text-xs">
            {complexity} complexity
          </span>
        )}
        {risk && (
          <span
            className={`px-2 py-1 rounded text-xs ${
              risk === "critical"
                ? "bg-red-100 text-red-800"
                : risk === "high"
                ? "bg-orange-100 text-orange-800"
                : "bg-green-100 text-green-800"
            }`}
          >
            {risk} risk
          </span>
        )}
        {ragStrategy && (
          <span className="px-2 py-1 bg-purple-100 text-purple-800 rounded text-xs">
            RAG: {ragStrategy}
          </span>
        )}
        {modelTier && (
          <span className="px-2 py-1 bg-yellow-100 text-yellow-800 rounded text-xs">
            model: {modelTier}
          </span>
        )}
        {guardrailBundle && (
          <span className="px-2 py-1 bg-[#0F1826] text-[#F0F6FF] rounded text-xs">
            guardrails: {guardrailBundle}
          </span>
        )}
        {assemblyLatencyMs !== undefined && (
          <span className="px-2 py-1 bg-[#0F1826] text-[#5A7494] rounded text-xs">
            profiled in {assemblyLatencyMs.toFixed(1)}ms
          </span>
        )}
      </div>

      {/* Active patterns */}
      {patternsActive && (
        <div className="mb-4">
          <p className="text-xs font-medium text-[#5A7494] mb-1">Active patterns:</p>
          <div className="flex flex-wrap gap-1">
            {Object.entries(patternsActive).flatMap(([category, patterns]) =>
              patterns.map((p) => (
                <span
                  key={`${category}:${p}`}
                  className="px-2 py-0.5 bg-indigo-50 text-indigo-700 rounded text-xs border border-indigo-200"
                >
                  {p}
                </span>
              ))
            )}
          </div>
        </div>
      )}

      {/* Decision trace */}
      {decisions.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-[#5A7494] hover:text-gray-700">
            Show {decisions.length} decisions
          </summary>
          <div className="mt-2 space-y-1">
            {decisions.map((d, i) => (
              <div key={i} className="text-xs border-l-2 border-blue-200 pl-2">
                <span className="font-medium text-[#A0B4CC]">{d.dimension}</span>
                {" → "}
                <span className="text-blue-700">{d.selected}</span>
                {d.reason && (
                  <span className="text-[#A0B4CC] ml-1">({d.reason})</span>
                )}
              </div>
            ))}
          </div>
        </details>
      )}

      {/* Eval score */}
      {overallScore !== null && overallScore !== undefined && (
        <div className="mt-3 pt-3 border-t">
          <div className="flex items-center gap-2">
            <span className="text-xs text-[#5A7494]">Eval score:</span>
            <div className="flex-1 h-1.5 bg-[#162035] rounded-full">
              <div
                className={`h-full rounded-full ${
                  overallScore >= 0.8
                    ? "bg-green-500"
                    : overallScore >= 0.6
                    ? "bg-yellow-500"
                    : "bg-red-500"
                }`}
                style={{ width: `${overallScore * 100}%` }}
              />
            </div>
            <span className="text-xs font-medium">
              {(overallScore * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export default RuntimeDecisionPanel;
