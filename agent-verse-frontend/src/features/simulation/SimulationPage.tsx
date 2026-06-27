import React, { useState } from 'react';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

interface SimulationResult {
  goal: string;
  summary?: {
    allowed_tools: string[];
    denied_tools: string[];
    requires_approval: string[];
    would_block_execution: boolean;
    hitl_approvals_needed: number;
  };
  policy_checks?: Array<{ tool: string; result: string }>;
  plan?: { steps: string[] };
}

export function SimulationPage() {
  const [goal, setGoal] = useState('');
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const apiKey =
    sessionStorage.getItem('av_api_key') ?? localStorage.getItem('av_api_key') ?? '';

  const runSimulation = async () => {
    if (!goal.trim()) return;
    setLoading(true);
    setError('');
    try {
      // Simulate governance policies
      const policyResp = await fetch(`${API_BASE}/governance/simulate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify({ goal }),
      });
      const policyData = policyResp.ok ? await policyResp.json() : {};

      // Get dry-run plan
      const planResp = await fetch(`${API_BASE}/goals`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify({ goal, dry_run: true }),
      });
      const planData = planResp.ok ? await planResp.json() : {};

      setResult({
        goal,
        summary: policyData.summary,
        policy_checks: policyData.policy_checks,
        plan: planData.plan || planData.execution_context?.plan,
      });
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Simulation Studio</h1>
      <p className="text-gray-600 mb-4">
        Test your goal against governance policies and see the planned execution steps before
        running.
      </p>

      <div className="mb-6">
        <label className="block text-sm font-medium mb-2">Goal to Simulate</label>
        <textarea
          value={goal}
          onChange={e => setGoal(e.target.value)}
          rows={3}
          className="w-full px-3 py-2 border rounded-lg text-sm"
          placeholder="e.g., Refund all failed payments from last week and notify merchants"
        />
        <button
          onClick={runSimulation}
          disabled={loading || !goal.trim()}
          className="mt-2 px-6 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 font-medium"
        >
          {loading ? 'Simulating...' : '▶ Run Simulation'}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded mb-4">
          {error}
        </div>
      )}

      {result && (
        <div className="space-y-6">
          {/* Policy Check Results */}
          {result.summary && (
            <div className="border rounded-lg p-4">
              <h2 className="font-semibold mb-3">Governance Policy Check</h2>
              <div
                className={`flex items-center gap-2 mb-3 p-2 rounded ${
                  result.summary.would_block_execution ? 'bg-red-50' : 'bg-green-50'
                }`}
              >
                <span
                  className={`font-medium ${
                    result.summary.would_block_execution ? 'text-red-700' : 'text-green-700'
                  }`}
                >
                  {result.summary.would_block_execution
                    ? '⛔ Execution Blocked'
                    : '✅ Execution Allowed'}
                </span>
                {result.summary.hitl_approvals_needed > 0 && (
                  <span className="text-amber-600 ml-2">
                    ⚠️ {result.summary.hitl_approvals_needed} approval(s) required
                  </span>
                )}
              </div>
              {result.summary.denied_tools.length > 0 && (
                <div className="text-red-600 text-sm">
                  <strong>Blocked tools:</strong> {result.summary.denied_tools.join(', ')}
                </div>
              )}
              {result.summary.requires_approval.length > 0 && (
                <div className="text-amber-600 text-sm mt-1">
                  <strong>Needs approval:</strong> {result.summary.requires_approval.join(', ')}
                </div>
              )}
            </div>
          )}

          {/* Planned Steps */}
          {result.plan?.steps && result.plan.steps.length > 0 && (
            <div className="border rounded-lg p-4">
              <h2 className="font-semibold mb-3">Planned Execution Steps</h2>
              <ol className="space-y-2">
                {result.plan.steps.map((step: string, i: number) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="bg-blue-100 text-blue-800 rounded-full w-6 h-6 flex items-center justify-center flex-shrink-0 text-xs font-bold">
                      {i + 1}
                    </span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default SimulationPage;
