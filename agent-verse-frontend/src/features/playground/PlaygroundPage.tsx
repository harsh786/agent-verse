import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Play, RotateCcw, Code } from 'lucide-react';
import { playgroundApi, PlaygroundResult } from '@/lib/api/client';

export function PlaygroundPage() {
  const [goal, setGoal] = useState('');
  const [mockTools, setMockTools] = useState('{\n  "jira.search_issues": [{"id": "BAU-1", "title": "Example issue"}]\n}');
  const [result, setResult] = useState<PlaygroundResult | null>(null);

  const simulate = useMutation({
    mutationFn: async () => {
      let tools: Record<string, unknown> = {};
      try { tools = JSON.parse(mockTools); } catch { throw new Error('Invalid JSON in mock tools'); }
      return playgroundApi.simulate(goal, tools);
    },
    onSuccess: data => setResult(data),
  });

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-bold">Agent Playground</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Test agent behavior safely with mocked tool responses
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: input */}
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1.5">Goal</label>
            <textarea
              value={goal}
              onChange={e => setGoal(e.target.value)}
              rows={4}
              placeholder="Describe what the agent should do..."
              className="w-full border border-input rounded-xl px-4 py-3 text-sm bg-background outline-none focus:ring-2 focus:ring-primary resize-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1.5 flex items-center gap-1.5">
              <Code className="h-4 w-4" /> Mock Tools (JSON)
            </label>
            <textarea
              value={mockTools}
              onChange={e => setMockTools(e.target.value)}
              rows={8}
              className="w-full border border-input rounded-xl px-4 py-3 text-xs font-mono bg-background outline-none focus:ring-2 focus:ring-primary resize-none"
            />
          </div>
          {simulate.isError && (
            <p className="text-sm text-red-500">{String(simulate.error)}</p>
          )}
          <div className="flex gap-2">
            <button
              onClick={() => simulate.mutate()}
              disabled={!goal.trim() || simulate.isPending}
              className="flex-1 flex items-center justify-center gap-2 bg-primary text-primary-foreground py-2.5 rounded-xl text-sm hover:opacity-90 disabled:opacity-50"
            >
              <Play className="h-4 w-4" />
              {simulate.isPending ? 'Simulating…' : 'Run Simulation'}
            </button>
            <button onClick={() => setResult(null)}
              className="border border-border px-4 py-2.5 rounded-xl text-sm hover:bg-accent">
              <RotateCcw className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Right: results */}
        <div className="space-y-4">
          {result ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className={`px-2.5 py-1 rounded-full text-xs font-semibold ${
                  result.status === 'complete' || result.status === 'completed'
                    ? 'bg-green-100 text-green-800'
                    : 'bg-yellow-100 text-yellow-800'
                }`}>{result.status}</span>
                {result.cost_usd != null && (
                  <span className="text-xs text-muted-foreground">
                    ~${result.cost_usd.toFixed(4)} simulated
                  </span>
                )}
              </div>
              {result.message && (
                <p className="text-sm text-muted-foreground">{result.message}</p>
              )}
              {result.steps && result.steps.length > 0 && (
                <div className="bg-muted/40 rounded-xl p-4 space-y-3">
                  <p className="text-xs font-semibold uppercase text-muted-foreground">
                    Execution Steps ({result.steps.length})
                  </p>
                  <ol className="space-y-3">
                    {result.steps.map((s, i) => (
                      <li key={i} className="text-sm">
                        <div className="flex items-start gap-2">
                          <span className="shrink-0 text-xs bg-primary/10 text-primary rounded-full w-5 h-5 flex items-center justify-center font-mono">
                            {i + 1}
                          </span>
                          <div>
                            <p className="font-medium">{s.step}</p>
                            {s.tool && (
                              <p className="text-xs text-muted-foreground font-mono mt-0.5">
                                → {s.tool}
                              </p>
                            )}
                            {s.output && (
                              <p className="text-xs text-muted-foreground mt-1 truncate">
                                {s.output}
                              </p>
                            )}
                          </div>
                        </div>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-64 text-muted-foreground">
              <Play className="h-12 w-12 mb-3 opacity-20" />
              <p className="text-sm">Run a simulation to see results here</p>
              <p className="text-xs mt-1">No real tools are called — safe to experiment</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
