import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Play, Shield, FlaskConical, BarChart3 } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { API_BASE } from '@/lib/api/client';

// ── Types ─────────────────────────────────────────────────────────────────────

interface RedTeamResult {
  case_id: string;
  name?: string;
  status: 'passed' | 'failed' | 'error' | string;
  details?: string;
}

interface RedTeamReport {
  total: number;
  passed: number;
  failed: number;
  results: RedTeamResult[];
  run_at?: string;
}

interface SimulationResult {
  goal_id?: string;
  status: string;
  steps?: Array<{ step: string; tool?: string; output?: string }>;
  cost_usd?: number;
  iterations?: number;
  message?: string;
}

interface EvalScorecard {
  goal_id: string;
  scores: Record<string, number>;
  average_score: number;
}

interface Suggestion {
  suggestion_id: string;
  category: string;
  description: string;
  confidence: number;
  applied: boolean;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function runRedTeam(apiKey: string): Promise<RedTeamReport> {
  const res = await fetch(`${API_BASE}/enterprise/red-team`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function runSimulation(
  apiKey: string,
  goal: string,
  mockTools: string
): Promise<SimulationResult> {
  let tools: unknown = {};
  try {
    tools = JSON.parse(mockTools || '{}');
  } catch {
    throw new Error('mock_tools must be valid JSON');
  }
  const res = await fetch(`${API_BASE}/enterprise/simulation`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
    body: JSON.stringify({ goal, mock_tools: tools }),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function runEvalApi(apiKey: string, goalId: string): Promise<EvalScorecard> {
  const res = await fetch(`${API_BASE}/goals/${goalId}/eval`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function fetchSuggestionsApi(apiKey: string): Promise<Suggestion[]> {
  const res = await fetch(`${API_BASE}/intelligence/suggestions?applied=false`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function applySuggestionApi(apiKey: string, id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/intelligence/suggestions/${id}/apply`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
}

async function rejectSuggestionApi(apiKey: string, id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/intelligence/suggestions/${id}/reject`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
}

async function fetchGoalsForEval(
  apiKey: string
): Promise<{ goals: Array<{ id: string; goal_id?: string; goal: string; status: string }> }> {
  const res = await fetch(`${API_BASE}/goals`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

// ── Eval Scorer constants ─────────────────────────────────────────────────────

const SCORE_DIMENSIONS = [
  'task_completion',
  'efficiency',
  'accuracy',
  'safety',
  'coherence',
] as const;

const SCORE_DIMENSION_COLORS: Record<string, string> = {
  task_completion: 'bg-blue-500',
  efficiency: 'bg-green-500',
  accuracy: 'bg-purple-500',
  safety: 'bg-orange-500',
  coherence: 'bg-teal-500',
};

const CATEGORY_BADGE_COLORS: Record<string, string> = {
  performance: 'bg-blue-100 text-blue-800',
  safety: 'bg-orange-100 text-orange-800',
  efficiency: 'bg-green-100 text-green-800',
  accuracy: 'bg-purple-100 text-purple-800',
  coherence: 'bg-teal-100 text-teal-800',
};

// ── Red Team section ──────────────────────────────────────────────────────────

function RedTeamSection({ apiKey }: { apiKey: string }) {
  const [report, setReport] = useState<RedTeamReport | null>(null);

  const mutation = useMutation({
    mutationFn: () => runRedTeam(apiKey),
    onSuccess: (data) => setReport(data),
  });

  const passRate = report
    ? Math.round((report.passed / (report.total || 1)) * 100)
    : 0;

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-5 py-4 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-muted-foreground" />
          <h2 className="font-semibold text-sm">Red Team Testing</h2>
        </div>
        <button
          onClick={() => mutation.mutate()}
          disabled={mutation.isPending}
          className="flex items-center gap-1.5 bg-primary text-primary-foreground px-3 py-1.5 rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
        >
          <Play className="h-3.5 w-3.5" />
          {mutation.isPending ? 'Running…' : 'Run Red Team'}
        </button>
      </div>

      <div className="p-5">
        {mutation.isError && (
          <p className="text-sm text-red-500 mb-4">{String(mutation.error)}</p>
        )}

        {report ? (
          <div className="space-y-4">
            {/* Summary */}
            <div className="grid grid-cols-3 gap-3">
              {[
                { label: 'Total Cases', value: report.total, color: 'text-foreground' },
                { label: 'Passed', value: report.passed, color: 'text-green-600' },
                { label: 'Failed', value: report.failed, color: 'text-red-600' },
              ].map(({ label, value, color }) => (
                <div
                  key={label}
                  className="bg-muted/40 rounded-lg p-3 text-center"
                >
                  <p className={`text-2xl font-bold ${color}`}>{value}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">{label}</p>
                </div>
              ))}
            </div>

            {/* Progress bar */}
            <div>
              <div className="flex justify-between text-xs text-muted-foreground mb-1">
                <span>Pass rate</span>
                <span>{passRate}%</span>
              </div>
              <div className="w-full bg-muted rounded-full h-2">
                <div
                  className={`h-2 rounded-full transition-all ${
                    passRate >= 80 ? 'bg-green-500' : passRate >= 50 ? 'bg-yellow-500' : 'bg-red-500'
                  }`}
                  style={{ width: `${passRate}%` }}
                />
              </div>
            </div>

            {/* Results table */}
            {report.results && report.results.length > 0 && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border">
                    {['Case', 'Status', 'Details'].map((h) => (
                      <th
                        key={h}
                        className="text-left py-2 font-medium text-muted-foreground text-xs"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {report.results.map((r, i) => (
                    <tr key={r.case_id ?? i} className="hover:bg-accent/50">
                      <td className="py-2.5 font-medium text-xs">{r.name ?? r.case_id}</td>
                      <td className="py-2.5">
                        <span
                          className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                            r.status === 'passed'
                              ? 'bg-green-100 text-green-800'
                              : r.status === 'failed'
                              ? 'bg-red-100 text-red-800'
                              : 'bg-yellow-100 text-yellow-800'
                          }`}
                        >
                          {r.status}
                        </span>
                      </td>
                      <td className="py-2.5 text-xs text-muted-foreground">
                        {r.details ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Run the red team suite to test your agent's resistance to adversarial
            inputs, prompt injection, and policy bypasses.
          </p>
        )}
      </div>
    </div>
  );
}

// ── Simulation section ────────────────────────────────────────────────────────

function SimulationSection({ apiKey }: { apiKey: string }) {
  const [goal, setGoal] = useState('');
  const [mockTools, setMockTools] = useState('{}');
  const [result, setResult] = useState<SimulationResult | null>(null);

  const mutation = useMutation({
    mutationFn: () => runSimulation(apiKey, goal, mockTools),
    onSuccess: (data) => setResult(data),
  });

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-5 py-4 border-b border-border flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-muted-foreground" />
        <h2 className="font-semibold text-sm">Goal Simulation</h2>
      </div>
      <div className="p-5 space-y-4">
        <div>
          <label className="block text-xs font-medium mb-1">Goal</label>
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Describe the goal to simulate…"
            rows={3}
            className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary resize-none"
          />
        </div>
        <div>
          <label className="block text-xs font-medium mb-1">Mock Tools (JSON)</label>
          <textarea
            value={mockTools}
            onChange={(e) => setMockTools(e.target.value)}
            placeholder='{"github:list_issues": [{"id": 1, "title": "Bug fix"}]}'
            rows={4}
            className="w-full border border-input rounded-lg px-3 py-2 text-sm font-mono bg-background outline-none focus:ring-2 focus:ring-primary resize-none"
          />
        </div>

        {mutation.isError && (
          <p className="text-sm text-red-500">{String(mutation.error)}</p>
        )}

        <div className="flex justify-end">
          <button
            onClick={() => mutation.mutate()}
            disabled={!goal.trim() || mutation.isPending}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
          >
            <Play className="h-3.5 w-3.5" />
            {mutation.isPending ? 'Simulating…' : 'Run Simulation'}
          </button>
        </div>

        {result && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span
                className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                  result.status === 'complete'
                    ? 'bg-green-100 text-green-800'
                    : result.status === 'failed'
                    ? 'bg-red-100 text-red-800'
                    : 'bg-blue-100 text-blue-800'
                }`}
              >
                {result.status}
              </span>
              {result.cost_usd != null && (
                <span className="text-xs text-muted-foreground">
                  ${result.cost_usd.toFixed(4)} simulated cost
                </span>
              )}
            </div>
            {result.message && (
              <p className="text-sm text-muted-foreground">{result.message}</p>
            )}
            {result.steps && result.steps.length > 0 && (
              <div className="bg-muted/40 rounded-lg p-3">
                <p className="text-xs font-medium mb-2">Simulated Steps</p>
                <ol className="space-y-1.5">
                  {result.steps.map((s, i) => (
                    <li key={i} className="text-xs">
                      <span className="font-medium">{i + 1}. {s.step}</span>
                      {s.tool && (
                        <span className="text-muted-foreground"> → {s.tool}</span>
                      )}
                      {s.output && (
                        <p className="text-muted-foreground mt-0.5 font-mono truncate">
                          {s.output}
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              </div>
            )}
            <details>
              <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground">
                Raw response
              </summary>
              <pre className="mt-2 text-xs font-mono text-muted-foreground overflow-auto">
                {JSON.stringify(result, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Eval Scorer section ───────────────────────────────────────────────────────

function EvalScorerSection({ apiKey }: { apiKey: string }) {
  const [selectedGoalId, setSelectedGoalId] = useState('');
  const [scorecard, setScorecard] = useState<EvalScorecard | null>(null);

  // Fetch goals for dropdown
  const { data: goalsData } = useQuery({
    queryKey: ['eval-goals'],
    queryFn: () => fetchGoalsForEval(apiKey),
    enabled: !!apiKey,
  });
  const goals = goalsData?.goals ?? [];

  // Fetch suggestions (unapplied)
  const {
    data: suggestions = [],
    isLoading: suggestionsLoading,
    refetch: refetchSuggestions,
  } = useQuery({
    queryKey: ['eval-suggestions'],
    queryFn: () => fetchSuggestionsApi(apiKey),
    enabled: !!apiKey,
  });

  const evalMutation = useMutation({
    mutationFn: () => runEvalApi(apiKey, selectedGoalId),
    onSuccess: (data) => setScorecard(data),
  });

  const applyMutation = useMutation({
    mutationFn: (id: string) => applySuggestionApi(apiKey, id),
    onSuccess: () => refetchSuggestions(),
  });

  const rejectMutation = useMutation({
    mutationFn: (id: string) => rejectSuggestionApi(apiKey, id),
    onSuccess: () => refetchSuggestions(),
  });

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-muted-foreground" />
          <h2 className="font-semibold text-sm">Eval Scorer</h2>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={selectedGoalId}
            onChange={(e) => setSelectedGoalId(e.target.value)}
            className="border border-input rounded-lg px-3 py-1.5 text-sm bg-background outline-none focus:ring-2 focus:ring-primary max-w-xs"
          >
            <option value="">Select a goal…</option>
            {goals.map((g) => {
              const id = g.goal_id ?? g.id;
              const label = g.goal.length > 60 ? `${g.goal.slice(0, 60)}…` : g.goal;
              return (
                <option key={id} value={id}>
                  {label}
                </option>
              );
            })}
          </select>
          <button
            onClick={() => evalMutation.mutate()}
            disabled={!selectedGoalId || evalMutation.isPending}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground px-3 py-1.5 rounded-lg text-sm hover:opacity-90 disabled:opacity-50"
          >
            <Play className="h-3.5 w-3.5" />
            {evalMutation.isPending ? 'Running…' : 'Run Eval'}
          </button>
        </div>
      </div>

      <div className="p-5 space-y-6">
        {evalMutation.isError && (
          <p className="text-sm text-red-500">{String(evalMutation.error)}</p>
        )}

        {/* Scorecard */}
        {scorecard ? (
          <div className="space-y-4">
            {/* Average score hero */}
            <div className="bg-muted/40 rounded-xl py-5 text-center">
              <p className="text-xs text-muted-foreground mb-1 uppercase tracking-wide">
                Average Score
              </p>
              <p className="text-5xl font-bold tabular-nums">
                {(scorecard.average_score * 100).toFixed(1)}
              </p>
              <p className="text-xs text-muted-foreground mt-1">out of 100</p>
            </div>

            {/* 5-dimension progress bars */}
            <div className="space-y-3">
              {SCORE_DIMENSIONS.map((dim) => {
                const raw = scorecard.scores[dim] ?? 0;
                const pct = Math.min(100, Math.max(0, Math.round(raw * 100)));
                return (
                  <div key={dim}>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="font-medium capitalize">
                        {dim.replace('_', ' ')}
                      </span>
                      <span className="text-muted-foreground tabular-nums">{pct}%</span>
                    </div>
                    <div className="w-full bg-muted rounded-full h-2">
                      <div
                        className={`h-2 rounded-full transition-all duration-500 ${
                          SCORE_DIMENSION_COLORS[dim] ?? 'bg-primary'
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            Select a goal and click <strong>Run Eval</strong> to score performance
            across 5 dimensions: task completion, efficiency, accuracy, safety, and coherence.
          </p>
        )}

        {/* Optimization Suggestions */}
        <div>
          <h3 className="font-semibold text-sm mb-3">Optimization Suggestions</h3>
          {suggestionsLoading ? (
            <p className="text-sm text-muted-foreground">Loading suggestions…</p>
          ) : suggestions.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No pending optimization suggestions.
            </p>
          ) : (
            <div className="space-y-3">
              {suggestions.map((s) => (
                <div
                  key={s.suggestion_id}
                  className="border border-border rounded-lg p-4 space-y-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium capitalize ${
                        CATEGORY_BADGE_COLORS[s.category] ??
                        'bg-muted text-muted-foreground'
                      }`}
                    >
                      {s.category}
                    </span>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {Math.round(s.confidence * 100)}% confidence
                    </span>
                  </div>
                  <p className="text-sm">{s.description}</p>
                  {/* Confidence bar */}
                  <div className="w-full bg-muted rounded-full h-1.5">
                    <div
                      className="h-1.5 rounded-full bg-primary/60 transition-all duration-300"
                      style={{ width: `${s.confidence * 100}%` }}
                    />
                  </div>
                  <div className="flex gap-2 pt-1">
                    <button
                      onClick={() => applyMutation.mutate(s.suggestion_id)}
                      disabled={
                        applyMutation.isPending || rejectMutation.isPending
                      }
                      className="px-3 py-1 bg-green-600 text-white text-xs rounded-md hover:bg-green-700 transition-colors disabled:opacity-50"
                    >
                      Apply
                    </button>
                    <button
                      onClick={() => rejectMutation.mutate(s.suggestion_id)}
                      disabled={
                        applyMutation.isPending || rejectMutation.isPending
                      }
                      className="px-3 py-1 bg-muted text-muted-foreground text-xs rounded-md hover:bg-accent transition-colors disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Eval Suites Section ───────────────────────────────────────────────────────

function EvalSuitesSection({ apiKey }: { apiKey: string }) {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [suiteName, setSuiteName] = useState('');
  const [suiteDesc, setSuiteDesc] = useState('');

  const { data: suites = [], isLoading } = useQuery({
    queryKey: ['eval-suites'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/eval/suites`, { headers: { 'X-API-Key': apiKey } });
      if (!res.ok) throw new Error(res.statusText);
      return res.json() as Promise<Array<{ suite_id: string; name: string; task_count: number; created_at: string }>>;
    },
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/eval/suites`, {
        method: 'POST',
        headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: suiteName, description: suiteDesc }),
      });
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-suites'] });
      setShowCreate(false);
      setSuiteName('');
      setSuiteDesc('');
    },
  });

  const runMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API_BASE}/eval/suites/${id}/run`, {
        method: 'POST',
        headers: { 'X-API-Key': apiKey },
      });
      if (!res.ok) throw new Error(res.statusText);
      return res.json();
    },
  });

  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-border">
        <h2 className="font-semibold text-sm">Eval Suites</h2>
        <button
          onClick={() => setShowCreate(true)}
          aria-label="Create suite"
          className="text-xs px-3 py-1.5 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
        >
          + Create suite
        </button>
      </div>

      {showCreate && (
        <div className="p-4 border-b space-y-3">
          <label className="block text-sm font-medium">
            Suite name
            <input
              aria-label="Suite name"
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              value={suiteName}
              onChange={(e) => setSuiteName(e.target.value)}
            />
          </label>
          <label className="block text-sm font-medium">
            Description
            <input
              className="mt-1 block w-full rounded border px-3 py-2 text-sm bg-background"
              value={suiteDesc}
              onChange={(e) => setSuiteDesc(e.target.value)}
            />
          </label>
          <div className="flex gap-2">
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending || !suiteName.trim()}
              className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-md disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating…' : 'Create'}
            </button>
            <button onClick={() => setShowCreate(false)} className="px-3 py-1.5 text-sm border rounded-md">Cancel</button>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="py-8 text-center text-sm text-muted-foreground">Loading suites…</div>
      ) : suites.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          No eval suites yet. Create one to group golden tasks and track regressions.
        </div>
      ) : (
        <div className="divide-y divide-border">
          {suites.map((suite) => (
            <div key={suite.suite_id} className="flex items-center justify-between p-4">
              <div>
                <p className="text-sm font-medium">{suite.name}</p>
                <p className="text-xs text-muted-foreground">
                  {suite.task_count} tasks · created {new Date(suite.created_at).toLocaleDateString()}
                </p>
              </div>
              <button
                onClick={() => runMutation.mutate(suite.suite_id)}
                disabled={runMutation.isPending}
                className="text-xs px-3 py-1 rounded border text-green-700 hover:bg-green-50 disabled:opacity-50"
              >
                Run
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function EvalPage() {
  const apiKey = useAuthStore((s) => s.apiKey);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Eval & Testing</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Red team testing, goal simulation, and eval scoring
        </p>
      </div>
      <RedTeamSection apiKey={apiKey} />
      <SimulationSection apiKey={apiKey} />
      <EvalScorerSection apiKey={apiKey} />
      <EvalSuitesSection apiKey={apiKey} />
    </div>
  );
}
