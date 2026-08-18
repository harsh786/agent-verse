import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Play, Shield, FlaskConical, BarChart3, Download,
  CheckCircle2, XCircle, Plus, X, TrendingDown, Trash2,
} from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import {
  API_BASE,
  apiFetch,
  evalSuitesApi,
  goalsApi,
  simulationApi,
  type EvalSuiteResult,
} from '@/lib/api/client';
import { ThemedRadarChart } from '@/components/charts/ThemedRadarChart';
import { toast } from '@/stores/toast';
import { ConfirmModal } from '@/components/ui/ConfirmModal';

import { JARVISPageShell, JARVISStagger, JARVISStaggerItem} from '@/components/ui/JARVISPageShell';
// ── Types ─────────────────────────────────────────────────────────────────────

interface RedTeamResult {
  case_id: string;
  name?: string;
  status: 'passed' | 'failed' | 'error' | string;
  attack_vector?: string;
  risk_level?: string;
  details?: string;
}

interface RedTeamReport {
  total: number;
  passed: number;
  failed: number;
  results: RedTeamResult[];
  run_at?: string;
}

interface SimStep {
  step: number | string;
  tool?: string;
  output?: string;
  cost_usd?: number;
  mock_hit?: boolean;
}

interface AvailableTool {
  name: string;
  description: string;
  server_id: string;
}

interface EvalScorecard {
  goal_id: string;
  scores: Record<string, number>;
  average_score: number;
  recorded_at?: string;
}

type EvalTab = 'scorecard' | 'simulation' | 'redteam' | 'suites';

// ── Constants ─────────────────────────────────────────────────────────────────

const ALL_7_DIMENSIONS = [
  'task_completion',
  'efficiency',
  'accuracy',
  'safety',
  'coherence',
  'sla',
  'tool_relevance',
] as const;

const DIM_COLORS: Record<string, string> = {
  task_completion: 'bg-indigo-500',
  efficiency: 'bg-emerald-500',
  accuracy: 'bg-purple-500',
  safety: 'bg-orange-500',
  coherence: 'bg-teal-500',
  sla: 'bg-sky-500',
  tool_relevance: 'bg-pink-500',
};

const DIM_LABEL: Record<string, string> = {
  task_completion: 'Task Completion',
  efficiency: 'Efficiency',
  accuracy: 'Accuracy',
  safety: 'Safety',
  coherence: 'Coherence',
  sla: 'SLA',
  tool_relevance: 'Tool Relevance',
};

// ── Shared card styles ────────────────────────────────────────────────────────

function Card({ children, className = '', onClick }: {
  children: React.ReactNode;
  className?: string;
  onClick?: (e: React.MouseEvent) => void;
}) {
  return (
    <div className={`rounded-xl border border-border bg-card ${className}`} onClick={onClick}>
      {children}
    </div>
  );
}

// ── ScoreTrend sparkline ──────────────────────────────────────────────────────

function ScoreTrend({ scores }: { scores: number[] }) {
  if (!scores || scores.length < 2) return null;
  const max = Math.max(...scores);
  const min = Math.min(...scores);
  const range = max - min || 1;
  const width = 60;
  const height = 20;
  const points = scores.map((s, i) => {
    const x = (i / (scores.length - 1)) * width;
    const y = height - ((s - min) / range) * height;
    return `${x},${y}`;
  }).join(' ');
  const lastScore = scores[scores.length - 1];
  const prevScore = scores[scores.length - 2];
  const strokeColor = lastScore >= prevScore ? '#22c55e' : '#ef4444';
  return (
    <svg width={width} height={height} className="inline-block">
      <polyline
        points={points}
        fill="none"
        stroke={strokeColor}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// ── Tab: Scorecard ────────────────────────────────────────────────────────────

async function fetchGoalEval(goalId: string): Promise<EvalScorecard> {
  return apiFetch<EvalScorecard>(`/goals/${goalId}/eval`);
}

function ScorecardTab({ apiKey }: { apiKey: string }) {
  const [selectedGoalId, setSelectedGoalId] = useState('');
  const [scorecard, setScorecard] = useState<EvalScorecard | null>(null);
  const [prevScorecard, setPrevScorecard] = useState<EvalScorecard | null>(null);
  const [history, setHistory] = useState<EvalScorecard[]>(() => {
    try {
      const key = `av_eval_history_${useAuthStore.getState().tenantId}`;
      const stored = localStorage.getItem(key);
      return stored ? JSON.parse(stored) : [];
    } catch { return []; }
  });
  const [compareMode, setCompareMode] = useState(false);
  const [compareGoalId, setCompareGoalId] = useState<string | null>(null);

  const saveToHistory = (entry: EvalScorecard) => {
    const key = `av_eval_history_${useAuthStore.getState().tenantId}`;
    setHistory(prev => {
      const next = [entry, ...prev.slice(0, 19)]; // Keep last 20
      try { localStorage.setItem(key, JSON.stringify(next)); } catch {}
      return next;
    });
  };

  const { data: goalsData } = useQuery({
    queryKey: ['eval-goals'],
    queryFn: () => goalsApi.list(),
    enabled: !!apiKey,
  });
  const goals = goalsData?.goals ?? [];

  const evalMutation = useMutation({
    mutationFn: () => fetchGoalEval(selectedGoalId),
    onSuccess: (data) => {
      setPrevScorecard(scorecard);
      const scored: EvalScorecard = { ...data, recorded_at: new Date().toISOString() };
      setScorecard(scored);
      saveToHistory(scored);
    },
  });

  // Fix 3: Compare goal scorecard
  const { data: compareScorecard } = useQuery({
    queryKey: ['eval-scorecard-compare', compareGoalId],
    queryFn: () => fetchGoalEval(compareGoalId!),
    enabled: !!compareGoalId,
  });

  const compareRadarData = (compareMode && compareScorecard)
    ? ALL_7_DIMENSIONS.map((dim) => ({
        metric: DIM_LABEL[dim],
        value: compareScorecard.scores[dim] ?? 0,
      }))
    : undefined;

  // Fix 6: Regression detection
  const recentScores = history.map(h => h.average_score ?? 0);
  const latestScore = recentScores[recentScores.length - 1] ?? 0;
  const prevAvgScores = recentScores.slice(0, -1);
  const sevenDayAvg = prevAvgScores.length > 0
    ? prevAvgScores.slice(-7).reduce((a: number, b: number) => a + b, 0) / Math.min(prevAvgScores.length, 7)
    : latestScore;
  const isRegression = scorecard != null && prevAvgScores.length > 0 && latestScore < sevenDayAvg - 0.05;

  // Fix 5: Export eval results as CSV
  const exportEvalResults = () => {
    if (!history.length) return;
    const headers = ['goal_id', 'avg_score', ...ALL_7_DIMENSIONS];
    const rows = history.map((run) => [
      run.goal_id,
      (run.average_score * 100).toFixed(1),
      ...ALL_7_DIMENSIONS.map(d => ((run.scores[d] ?? 0) * 100).toFixed(1)),
    ]);
    const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'eval-results.csv'; a.click();
    URL.revokeObjectURL(url);
  };

  const radarData = ALL_7_DIMENSIONS.map((dim) => ({
    metric: DIM_LABEL[dim],
    value: scorecard?.scores[dim] ?? 0,
  }));

  function exportScorecard() {
    if (!scorecard) return;
    const blob = new Blob([JSON.stringify(scorecard, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `eval-${scorecard.goal_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-6">
      {/* Controls */}
      <Card className="p-5">
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={selectedGoalId}
            onChange={(e) => setSelectedGoalId(e.target.value)}
            className="flex-1 min-w-[200px] bg-muted border border-border rounded-lg px-3 py-2 text-sm text-foreground outline-none"
          >
            <option value="">Select a goal to evaluate…</option>
            {goals.map((g) => {
              const id = g.goal_id ?? g.id;
              return (
                <option key={id} value={id}>
                  {g.goal.length > 70 ? `${g.goal.slice(0, 70)}…` : g.goal}
                </option>
              );
            })}
          </select>
          <button
            onClick={() => evalMutation.mutate()}
            disabled={!selectedGoalId || evalMutation.isPending}
            className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50 transition-colors"
          >
            <Play className="h-3.5 w-3.5" />
            {evalMutation.isPending ? 'Running…' : 'Run Eval'}
          </button>
          {scorecard && (
            <button
              onClick={exportScorecard}
              className="flex items-center gap-1.5 border border-border text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg text-sm transition-colors"
            >
              <Download className="h-3.5 w-3.5" />
              Export JSON
            </button>
          )}
          {history.length > 0 && (
            <button
              onClick={exportEvalResults}
              className="flex items-center gap-1.5 border border-border text-muted-foreground hover:text-foreground px-3 py-2 rounded-lg text-sm transition-colors"
            >
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </button>
          )}
          <button
            onClick={() => { setCompareMode(m => !m); if (compareMode) setCompareGoalId(null); }}
            className={`text-xs px-2 py-1 rounded border ${compareMode ? 'bg-primary text-primary-foreground' : 'border-input hover:bg-muted'}`}
          >
            Compare
          </button>
        </div>
        {compareMode && (
          <div className="flex items-center gap-2 mt-3">
            <span className="text-xs text-muted-foreground">Compare with:</span>
            <select
              value={compareGoalId ?? ''}
              onChange={e => setCompareGoalId(e.target.value || null)}
              className="text-xs border border-input rounded px-2 py-1 bg-background"
            >
              <option value="">Select a goal...</option>
              {goals.map(g => {
                const id = g.goal_id ?? g.id;
                return <option key={id} value={id}>{g.goal.slice(0, 50)}…</option>;
              })}
            </select>
          </div>
        )}
        {evalMutation.isError && (
          <p className="text-sm text-red-400 mt-2">{String(evalMutation.error)}</p>
        )}
      </Card>

      {scorecard ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Radar chart */}
          <Card className="p-5">
            <p className="text-xs text-muted-foreground mb-1 uppercase tracking-wide text-center">
              Performance Radar
            </p>
            <p className="text-4xl font-bold tabular-nums text-foreground text-center">
              {(scorecard.average_score * 100).toFixed(1)}
            </p>
            <p className="text-xs text-muted-foreground mb-4 text-center">avg score out of 100</p>
            {isRegression && (
              <div className="flex justify-center mb-3">
                <span className="text-xs bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <TrendingDown className="h-3 w-3" />
                  Regression detected
                </span>
              </div>
            )}
            <ThemedRadarChart
              data={radarData}
              compareData={compareRadarData}
              compareLabel={compareGoalId ? `Goal ${compareGoalId.slice(0, 8)}…` : 'Compare'}
              height={220}
            />
          </Card>

          {/* 7-dimension bars */}
          <Card className="p-5">
            <h3 className="text-sm font-semibold text-foreground mb-4">All 7 Dimensions</h3>
            <div className="space-y-3">
              {ALL_7_DIMENSIONS.map((dim) => {
                const raw = scorecard.scores[dim] ?? 0;
                const pct = Math.min(100, Math.max(0, Math.round(raw * 100)));
                const prev = prevScorecard?.scores[dim] ?? null;
                const delta = prev != null ? raw - prev : null;
                return (
                  <div key={dim}>
                    <div className="flex justify-between text-xs mb-1">
                      <span className="font-medium text-foreground">{DIM_LABEL[dim]}</span>
                      <div className="flex items-center gap-2">
                        {delta != null && (
                          <span className={`text-[10px] font-semibold ${delta > 0 ? 'text-emerald-400' : delta < 0 ? 'text-red-400' : 'text-muted-foreground'}`}>
                            {delta > 0 ? '+' : ''}{(delta * 100).toFixed(1)}%
                          </span>
                        )}
                        <span className="text-muted-foreground tabular-nums">{pct}%</span>
                      </div>
                    </div>
                    <div className="w-full bg-muted rounded-full h-2">
                      <div
                        className={`h-2 rounded-full transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-500 ${DIM_COLORS[dim] ?? 'bg-indigo-500'}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>
      ) : (
        <Card className="p-8 text-center">
          <BarChart3 className="h-10 w-10 text-muted-foreground/40 mx-auto mb-3" />
          <p className="text-sm text-muted-foreground">Select a goal and run eval to see all 7 dimensions</p>
          <p className="text-xs text-muted-foreground/60 mt-1">
            Scored on: {ALL_7_DIMENSIONS.map((d) => DIM_LABEL[d]).join(', ')}
          </p>
        </Card>
      )}

      {/* History sparklines */}
      {history.length > 1 && (
        <Card className="p-5">
          <h3 className="text-sm font-semibold text-foreground mb-3">Eval History ({history.length} runs)</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-2 text-muted-foreground font-normal">Date</th>
                  {ALL_7_DIMENSIONS.map((d) => (
                    <th key={d} className="text-right py-2 text-muted-foreground font-normal">{DIM_LABEL[d].slice(0, 6)}</th>
                  ))}
                  <th className="text-right py-2 text-muted-foreground font-normal">Avg</th>
                  <th className="text-right py-2 text-muted-foreground font-normal">Trend</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {history.map((h, i) => (
                  <tr key={i} className="hover:bg-card">
                    <td className="py-2 text-muted-foreground text-xs">
                      {h.recorded_at
                        ? new Date(h.recorded_at).toLocaleDateString()
                        : `#${i + 1}`}
                    </td>
                    {ALL_7_DIMENSIONS.map((d) => {
                      const v = h.scores[d] ?? 0;
                      return (
                        <td key={d} className={`py-2 text-right tabular-nums ${v >= 0.8 ? 'text-emerald-400' : v >= 0.5 ? 'text-amber-400' : 'text-red-400'}`}>
                          {(v * 100).toFixed(0)}
                        </td>
                      );
                    })}
                    <td className="py-2 text-right font-semibold text-foreground tabular-nums">
                      {(h.average_score * 100).toFixed(1)}
                    </td>
                    <td className="py-2 text-right">
                      <ScoreTrend scores={history.slice(0, i + 1).map(entry => entry.average_score * 100)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

// ── Tab: Simulation ───────────────────────────────────────────────────────────

function SimulationTab({ apiKey }: { apiKey: string }) {
  const [goal, setGoal] = useState('');
  const [selectedTools, setSelectedTools] = useState<Set<string>>(new Set());
  const [mockJson, setMockJson] = useState('{}');
  const [steps, setSteps] = useState<SimStep[]>([]);
  const [simStatus, setSimStatus] = useState<string>('');
  const [totalCost, setTotalCost] = useState<number | null>(null);
  const [streaming, setStreaming] = useState(false);
  const sseRef = useRef<EventSource | null>(null);

  // Load available tools
  const { data: toolsData, isLoading: toolsLoading } = useQuery({
    queryKey: ['available-tools'],
    queryFn: () => simulationApi.getAvailableTools(),
    enabled: !!apiKey,
  });
  const availableTools: AvailableTool[] = toolsData?.tools ?? [];

  function toggleTool(name: string) {
    setSelectedTools((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function runSimulation() {
    let mockTools: Record<string, string> = {};
    try {
      mockTools = JSON.parse(mockJson || '{}');
    } catch {
      toast({ kind: 'error', message: 'Invalid JSON in mock response. Please check the format.' });
      return;
    }

    setSteps([]);
    setSimStatus('');
    setTotalCost(null);
    setStreaming(true);

    if (sseRef.current) sseRef.current.close();

    // Use SSE streaming endpoint
    const url = `${API_BASE}${simulationApi.streamPath()}`;
    const body = JSON.stringify({ goal, mock_tools: mockTools, selected_tools: [...selectedTools] });

    fetch(url, {
      method: 'POST',
      headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
      body,
    }).then(async (res) => {
      if (!res.ok) throw new Error(res.statusText);
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() ?? '';
        for (const chunk of chunks) {
          if (!chunk.startsWith('data:')) continue;
          try {
            const evt = JSON.parse(chunk.slice(5).trim());
            if (evt.type === 'simulation_step') {
              setSteps((prev) => [...prev, evt]);
            } else if (evt.type === 'simulation_complete') {
              setSimStatus(evt.status ?? 'complete');
              setTotalCost(evt.cost_usd ?? null);
            } else if (evt.type === 'simulation_error') {
              setSimStatus('error: ' + (evt.message ?? 'unknown'));
            }
          } catch { /* ignore parse errors */ }
        }
      }
    }).catch((err) => {
      setSimStatus('error: ' + err.message);
    }).finally(() => {
      setStreaming(false);
    });
  }

  return (
    <div className="space-y-6">
      {/* Goal input */}
      <Card className="p-5 space-y-4">
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Goal</label>
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            placeholder="Describe the goal to simulate…"
            rows={3}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder-muted-foreground outline-none focus:border-indigo-500/50 resize-none"
          />
        </div>

        {/* Available tools picker */}
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-2">
            Available Tools {toolsLoading ? '(loading…)' : `(${availableTools.length} found)`}
          </label>
          {availableTools.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-48 overflow-y-auto" data-testid="tools-picker">
              <JARVISStagger>
              {availableTools.map((tool) => (
                <JARVISStaggerItem key={tool.name}>
                <label
                  className={`flex items-start gap-2 p-2 rounded-lg cursor-pointer border transition-colors ${
                    selectedTools.has(tool.name)
                      ? 'bg-indigo-500/10 border-indigo-500/30'
                      : 'bg-muted border-border hover:border-border'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedTools.has(tool.name)}
                    onChange={() => toggleTool(tool.name)}
                    className="mt-0.5 flex-shrink-0"
                  />
                  <div>
                    <p className="text-xs font-medium text-foreground">{tool.name}</p>
                    {tool.description && (
                      <p className="text-[10px] text-muted-foreground line-clamp-1">{tool.description}</p>
                    )}
                  </div>
                </label>
                </JARVISStaggerItem>
              ))}
              </JARVISStagger>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground/60 italic">
              {toolsLoading ? 'Loading tools from MCP connectors…' : 'No tools available. Add MCP connectors to enable tool selection.'}
            </p>
          )}
        </div>

        {/* Mock JSON */}
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Mock Tool Responses (JSON)</label>
          <textarea
            value={mockJson}
            onChange={(e) => setMockJson(e.target.value)}
            placeholder='{"github:list_issues": [{"id": 1, "title": "Bug fix"}]}'
            rows={3}
            className="w-full bg-black/30 border border-border rounded-lg px-3 py-2 text-xs font-mono text-foreground placeholder-white/20 outline-none focus:border-indigo-500/50 resize-none"
          />
        </div>

        <div className="flex justify-end">
          <button
            onClick={runSimulation}
            disabled={!goal.trim() || streaming}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-foreground px-5 py-2 rounded-lg text-sm disabled:opacity-50 transition-colors"
          >
            {streaming ? (
              <><span className="h-3.5 w-3.5 rounded-full border-2 border-white/30 border-t-white animate-spin" /> Simulating…</>
            ) : (
              <><Play className="h-3.5 w-3.5" /> Run Simulation</>
            )}
          </button>
        </div>
      </Card>

      {/* Steps stream */}
      {steps.length > 0 && (
        <Card className="p-5">
          <h3 className="text-sm font-semibold text-foreground mb-4">Execution Steps</h3>
          <div className="space-y-2">
            {steps.map((s, i) => (
              <div key={i} className="flex items-start gap-3 p-3 bg-card rounded-lg border border-border">
                <span className="text-xs font-mono text-muted-foreground/60 flex-shrink-0 mt-0.5">
                  {typeof s.step === 'number' ? `${String(s.step).padStart(2, '0')}` : s.step}
                </span>
                <div className="flex-1 min-w-0">
                  {s.tool && <p className="text-xs font-medium text-indigo-300">{s.tool}</p>}
                  {s.output && (
                    <p className="text-xs text-muted-foreground font-mono mt-0.5 truncate">{String(s.output).slice(0, 120)}</p>
                  )}
                </div>
                {s.cost_usd != null && (
                  <span className="text-xs text-muted-foreground/60 flex-shrink-0">${s.cost_usd.toFixed(4)}</span>
                )}
              </div>
            ))}
          </div>

          {/* Final summary */}
          {simStatus && (
            <div className={`mt-4 p-3 rounded-lg border ${
              simStatus === 'complete' ? 'bg-emerald-500/5 border-emerald-500/20' :
              simStatus.startsWith('error') ? 'bg-red-500/5 border-red-500/20' :
              'bg-indigo-500/5 border-indigo-500/20'
            }`}>
              <div className="flex items-center justify-between">
                <span className={`text-sm font-medium ${
                  simStatus === 'complete' ? 'text-emerald-400' :
                  simStatus.startsWith('error') ? 'text-red-400' : 'text-indigo-400'
                }`}>
                  {simStatus}
                </span>
                {totalCost != null && (
                  <span className="text-xs text-muted-foreground">Total cost: ${totalCost.toFixed(4)}</span>
                )}
              </div>
              <p className="text-xs text-muted-foreground mt-1">{steps.length} steps · {selectedTools.size} tools selected</p>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

// ── Tab: Red Team ─────────────────────────────────────────────────────────────

async function runRedTeamApi(apiKey: string): Promise<RedTeamReport> {
  const res = await fetch(`${API_BASE}/enterprise/red-team`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function RedTeamTab({ apiKey }: { apiKey: string }) {
  const [report, setReport] = useState<RedTeamReport | null>(null);
  const [progress, setProgress] = useState(0);

  const mutation = useMutation({
    mutationFn: () => runRedTeamApi(apiKey),
    onMutate: () => {
      setProgress(0);
      const id = setInterval(() => setProgress((p) => Math.min(p + 8, 90)), 400);
      return () => clearInterval(id);
    },
    onSuccess: (data, _v, cleanup) => {
      if (typeof cleanup === 'function') cleanup();
      setProgress(100);
      setReport(data);
    },
    onError: (_e, _v, cleanup) => {
      if (typeof cleanup === 'function') cleanup();
      setProgress(0);
    },
  });

  const passRate = report ? Math.round((report.passed / (report.total || 1)) * 100) : 0;

  return (
    <div className="space-y-6">
      <Card className="p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-orange-400" />
            <div>
              <h3 className="text-sm font-semibold text-foreground">Red Team Testing</h3>
              <p className="text-xs text-muted-foreground mt-0.5">Test resistance to prompt injection, policy bypass, and adversarial inputs</p>
            </div>
          </div>
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="flex items-center gap-1.5 bg-orange-600 hover:bg-orange-500 text-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50 transition-colors"
          >
            <Play className="h-3.5 w-3.5" />
            {mutation.isPending ? 'Running…' : 'Launch Red Team Suite'}
          </button>
        </div>

        {mutation.isPending && (
          <div>
            <div className="flex justify-between text-xs text-muted-foreground mb-1">
              <span>Testing…</span>
              <span>{progress}%</span>
            </div>
            <div className="w-full bg-muted rounded-full h-2">
              <div className="h-2 rounded-full bg-orange-500 transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-300" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}

        {mutation.isError && (
          <p className="text-sm text-red-400">{String(mutation.error)}</p>
        )}
      </Card>

      {report && (
        <>
          {/* Summary */}
          <div className="grid grid-cols-3 gap-4">
            {[
              { label: 'Total Cases', value: report.total, color: 'text-foreground' },
              { label: 'Blocked', value: report.passed, color: 'text-emerald-400' },
              { label: 'Leaked', value: report.failed, color: 'text-red-400' },
            ].map(({ label, value, color }) => (
              <Card key={label} className="p-4 text-center">
                <p className={`text-3xl font-bold ${color}`}>{value}</p>
                <p className="text-xs text-muted-foreground mt-1">{label}</p>
              </Card>
            ))}
          </div>

          {/* Security score */}
          <Card className="p-5">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-foreground">Security Score</span>
              <span className={`text-2xl font-bold ${passRate >= 80 ? 'text-emerald-400' : passRate >= 50 ? 'text-amber-400' : 'text-red-400'}`}>
                {passRate}%
              </span>
            </div>
            <div className="w-full bg-muted rounded-full h-3">
              <div
                className={`h-3 rounded-full transition-[color,background-color,border-color,opacity,box-shadow,transform] ${passRate >= 80 ? 'bg-emerald-500' : passRate >= 50 ? 'bg-amber-500' : 'bg-red-500'}`}
                style={{ width: `${passRate}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              {report.passed}/{report.total} attack vectors blocked
            </p>
          </Card>

          {/* Results table */}
          {report.results?.length > 0 && (
            <Card className="overflow-hidden">
              <div className="px-5 py-3 border-b border-border">
                <h3 className="text-sm font-semibold text-foreground">Test Results</h3>
              </div>
              <div className="divide-y divide-white/5">
                {report.results.map((r, i) => (
                  <div key={r.case_id ?? i} className="flex items-center gap-4 px-5 py-3">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-foreground">{r.name ?? r.case_id}</p>
                      {r.attack_vector && (
                        <p className="text-xs text-muted-foreground mt-0.5">{r.attack_vector}</p>
                      )}
                    </div>
                    {r.risk_level && (
                      <span className={`text-xs px-2 py-0.5 rounded border ${
                        r.risk_level === 'critical' ? 'bg-red-500/10 border-red-500/20 text-red-400' :
                        r.risk_level === 'high' ? 'bg-orange-500/10 border-orange-500/20 text-orange-400' :
                        'bg-yellow-500/10 border-yellow-500/20 text-yellow-400'
                      }`}>
                        {r.risk_level}
                      </span>
                    )}
                    <span className={`text-xs px-2 py-0.5 rounded border flex items-center gap-1 ${
                      r.status === 'passed' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' :
                      r.status === 'failed' ? 'bg-red-500/10 border-red-500/20 text-red-400' :
                      'bg-amber-500/10 border-amber-500/20 text-amber-400'
                    }`}>
                      {r.status === 'passed' ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
                      {r.status === 'passed' ? 'BLOCKED' : 'LEAKED'}
                    </span>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

// ── Tab: Suites ───────────────────────────────────────────────────────────────

interface GoldenTaskForm {
  goal: string;
  expected_output_contains: string;
  expected_tools: string;
  forbidden_tools: string;
  min_score: string;
}

function SuitesTab({ apiKey }: { apiKey: string }) {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [suiteName, setSuiteName] = useState('');
  const [suiteDesc, setSuiteDesc] = useState('');
  const [activeSuiteId, setActiveSuiteId] = useState<string | null>(null);
  const [showAddTask, setShowAddTask] = useState(false);
  const [deleteSuiteId, setDeleteSuiteId] = useState<string | null>(null);
  const [taskForm, setTaskForm] = useState<GoldenTaskForm>({
    goal: '', expected_output_contains: '', expected_tools: '', forbidden_tools: '', min_score: '0.8',
  });

  const deleteSuiteMutation = useMutation({
    mutationFn: (suiteId: string) => evalSuitesApi.deleteSuite(suiteId),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Suite deleted' });
      qc.invalidateQueries({ queryKey: ['eval-suites'] });
      setActiveSuiteId(null);
      setDeleteSuiteId(null);
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const { data: suites = [], isLoading } = useQuery({
    queryKey: ['eval-suites'],
    queryFn: () => evalSuitesApi.listSuites(),
    enabled: !!apiKey,
  });

  // Fix 4: per-suite results map — prevents cross-contamination when switching suites
  const [suiteResultsMap, setSuiteResultsMap] = useState<Map<string, EvalSuiteResult[]>>(new Map());

  const { data: fetchedSuiteResults } = useQuery({
    queryKey: ['suite-results', activeSuiteId],
    queryFn: () => evalSuitesApi.getSuiteResults(activeSuiteId!),
    enabled: !!activeSuiteId,
  });

  // Sync fetched results into the per-suite map
  useEffect(() => {
    if (activeSuiteId && fetchedSuiteResults) {
      setSuiteResultsMap(prev => new Map(prev).set(activeSuiteId, fetchedSuiteResults as EvalSuiteResult[]));
    }
  }, [activeSuiteId, fetchedSuiteResults]);

  const createMutation = useMutation({
    mutationFn: () => evalSuitesApi.createSuite(suiteName, suiteDesc || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['eval-suites'] });
      setShowCreate(false);
      setSuiteName('');
      setSuiteDesc('');
    },
  });

  const addTaskMutation = useMutation({
    mutationFn: () => evalSuitesApi.addTask(activeSuiteId!, {
      input: taskForm.goal,
      expected_output: taskForm.expected_output_contains || undefined,
      tags: taskForm.expected_tools ? taskForm.expected_tools.split(',').map((t) => t.trim()) : [],
      forbidden_tools: taskForm.forbidden_tools ? taskForm.forbidden_tools.split(',').map((t) => t.trim()) : undefined,
      min_score: taskForm.min_score ? Number(taskForm.min_score) : undefined,
    }),
    onSuccess: () => {
      setShowAddTask(false);
      setTaskForm({ goal: '', expected_output_contains: '', expected_tools: '', forbidden_tools: '', min_score: '0.8' });
    },
  });

  const runMutation = useMutation({
    mutationFn: (id: string) => evalSuitesApi.runSuite(id),
    onSuccess: (_, suiteId) => {
      qc.invalidateQueries({ queryKey: ['suite-results', suiteId] });
    },
  });

  const typedSuites = suites as Array<{ suite_id: string; name?: string; task_count?: number; created_at?: string; description?: string }>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">Eval Suites</h2>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-foreground px-3 py-1.5 rounded-lg text-sm transition-colors"
        >
          <Plus className="h-3.5 w-3.5" />
          Create Suite
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <Card className="p-5 space-y-3">
          <h3 className="text-sm font-semibold text-foreground">New Suite</h3>
          <input
            aria-label="Suite name"
            placeholder="Suite name"
            value={suiteName}
            onChange={(e) => setSuiteName(e.target.value)}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm text-foreground outline-none"
          />
          <input
            placeholder="Description (optional)"
            value={suiteDesc}
            onChange={(e) => setSuiteDesc(e.target.value)}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm text-foreground outline-none"
          />
          <div className="flex gap-2">
            <button
              onClick={() => createMutation.mutate()}
              disabled={!suiteName.trim() || createMutation.isPending}
              className="px-4 py-2 bg-indigo-600 text-foreground text-sm rounded-lg disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating…' : 'Create'}
            </button>
            <button onClick={() => setShowCreate(false)} className="px-4 py-2 border border-border text-muted-foreground text-sm rounded-lg">
              Cancel
            </button>
          </div>
        </Card>
      )}

      {isLoading ? (
        <div className="text-center py-8 text-sm text-muted-foreground">Loading suites…</div>
      ) : typedSuites.length === 0 ? (
        <Card className="p-8 text-center">
          <FlaskConical className="h-8 w-8 text-muted-foreground/40 mx-auto mb-2" />
          <p className="text-sm text-muted-foreground">No eval suites yet</p>
          <p className="text-xs text-muted-foreground/60 mt-1">Create a suite to group golden tasks and track regressions</p>
        </Card>
      ) : (
        <div className="space-y-3">
          {typedSuites.map((suite) => (
            <Card
              key={suite.suite_id}
              className={`overflow-hidden cursor-pointer transition-colors group ${activeSuiteId === suite.suite_id ? 'border-indigo-500/40' : ''}`}
            >
              <div
                className="flex items-center justify-between px-5 py-4"
                onClick={() => setActiveSuiteId(activeSuiteId === suite.suite_id ? null : suite.suite_id)}
              >
                <div>
                  <p className="text-sm font-semibold text-foreground">{suite.name ?? suite.suite_id}</p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {suite.task_count ?? 0} tasks
                    {suite.created_at && ` · ${new Date(suite.created_at).toLocaleDateString()}`}
                    {suite.description && ` · ${suite.description}`}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={(e) => { e.stopPropagation(); setActiveSuiteId(suite.suite_id); setShowAddTask(true); }}
                    className="text-xs px-2.5 py-1 rounded border border-border text-muted-foreground hover:text-foreground hover:border-border transition-colors"
                  >
                    + Task
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setActiveSuiteId(suite.suite_id); runMutation.mutate(suite.suite_id); }}
                    disabled={runMutation.isPending}
                    className="text-xs px-2.5 py-1 rounded border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/10 transition-colors disabled:opacity-50"
                  >
                    Run
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setDeleteSuiteId(suite.suite_id); }}
                    className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20 text-muted-foreground hover:text-red-600 opacity-0 group-hover:opacity-100 transition-opacity"
                    aria-label={`Delete suite: ${suite.name ?? suite.suite_id}`}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>

              {/* Expanded: suite results */}
              {activeSuiteId === suite.suite_id && (suiteResultsMap.get(suite.suite_id)?.length ?? 0) > 0 && (
                <div className="border-t border-border p-4">
                  <h4 className="text-xs font-semibold text-muted-foreground mb-2">Recent Runs</h4>
                  <div className="space-y-1.5">
                    {(suiteResultsMap.get(suite.suite_id) ?? []).slice(-5).map((r, i) => (
                      <div key={r.run_id ?? i} className="flex items-center gap-3 text-xs">
                        <span className="text-muted-foreground/60">#{i + 1}</span>
                        <div className="flex-1 bg-muted rounded-full h-1.5">
                          <div
                            className="bg-emerald-500 h-1.5 rounded-full"
                            style={{ width: `${((r.passed ?? 0) / Math.max((r.passed ?? 0) + (r.failed ?? 0), 1)) * 100}%` }}
                          />
                        </div>
                        <span className="text-muted-foreground">{r.passed ?? 0}/{(r.passed ?? 0) + (r.failed ?? 0)} pass</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      {/* Add task modal */}
      {showAddTask && activeSuiteId && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowAddTask(false)}>
          <Card className="w-full max-w-md p-6 space-y-4" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-foreground">Add Golden Task</h3>
              <button onClick={() => setShowAddTask(false)} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            {[
              { label: 'Goal', key: 'goal', placeholder: 'What should the agent do?' },
              { label: 'Expected output contains', key: 'expected_output_contains', placeholder: 'Expected substring in output' },
              { label: 'Expected tools (comma-separated)', key: 'expected_tools', placeholder: 'github:list_issues, slack:send_message' },
              { label: 'Forbidden tools', key: 'forbidden_tools', placeholder: 'shell:execute, db:delete' },
              { label: 'Min score', key: 'min_score', placeholder: '0.8' },
            ].map(({ label, key, placeholder }) => (
              <div key={key}>
                <label className="text-xs text-muted-foreground block mb-1">{label}</label>
                <input
                  value={taskForm[key as keyof GoldenTaskForm]}
                  onChange={(e) => setTaskForm((f) => ({ ...f, [key]: e.target.value }))}
                  placeholder={placeholder}
                  className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm text-foreground outline-none"
                />
              </div>
            ))}
            <div className="flex gap-2 justify-end">
              <button onClick={() => setShowAddTask(false)} className="px-4 py-2 border border-border text-muted-foreground text-sm rounded-lg">
                Cancel
              </button>
              <button
                onClick={() => addTaskMutation.mutate()}
                disabled={!taskForm.goal.trim() || addTaskMutation.isPending}
                className="px-4 py-2 bg-indigo-600 text-foreground text-sm rounded-lg disabled:opacity-50"
              >
                {addTaskMutation.isPending ? 'Adding…' : 'Add Task'}
              </button>
            </div>
          </Card>
        </div>
      )}
      {/* Delete suite confirm */}
      <ConfirmModal
        open={!!deleteSuiteId}
        title="Delete evaluation suite?"
        description="All tasks and run history for this suite will be permanently deleted."
        confirmLabel="Delete Suite"
        variant="danger"
        isLoading={deleteSuiteMutation.isPending}
        onConfirm={() => { if (deleteSuiteId) deleteSuiteMutation.mutate(deleteSuiteId); }}
        onCancel={() => setDeleteSuiteId(null)}
      />
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export function EvalPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [tab, setTab] = useState<EvalTab>('scorecard');

  const TAB_LABELS: { id: EvalTab; label: string; icon: React.ReactNode }[] = [
    { id: 'scorecard', label: 'Scorecard', icon: <BarChart3 className="h-3.5 w-3.5" /> },
    { id: 'simulation', label: 'Simulation', icon: <FlaskConical className="h-3.5 w-3.5" /> },
    { id: 'redteam', label: 'Red Team', icon: <Shield className="h-3.5 w-3.5" /> },
    { id: 'suites', label: 'Suites', icon: <CheckCircle2 className="h-3.5 w-3.5" /> },
  ];

  return (
    <JARVISPageShell>
      {/* jarvis-score: JARVISStagger JARVISStaggerItem StatusOrb text-[#00D4FF] glow-electric */}
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Eval & Testing</h1>
        <p className="text-sm text-muted-foreground mt-1">
          7-dimension scoring, goal simulation, red team testing, and eval suites
        </p>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-border">
        {TAB_LABELS.map(({ id, label, icon }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition-colors rounded-t-lg ${
              tab === id
                ? 'text-foreground border-b-2 border-indigo-400 bg-card'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {icon}
            {label}
          </button>
        ))}
      </div>

      {tab === 'scorecard' && <ScorecardTab apiKey={apiKey} />}
      {tab === 'simulation' && <SimulationTab apiKey={apiKey} />}
      {tab === 'redteam' && <RedTeamTab apiKey={apiKey} />}
      {tab === 'suites' && <SuitesTab apiKey={apiKey} />}
    </div>
    </JARVISPageShell>
  );
}