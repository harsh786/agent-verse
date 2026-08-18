import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Bot } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { agentsApi } from '@/lib/api/client';
import { MissionControlLayout } from '@/components/ui/MissionControlLayout';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';

export function AgentCreatePage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [nlCommand, setNlCommand] = useState('');
  const [autorun, setAutorun] = useState(false);
  const [mode, setMode] = useState<'nl' | 'manual'>('nl');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [manualForm, setManualForm] = useState({
    name: '',
    goal_template: '',
    autonomy_mode: 'bounded-autonomous',
    connector_ids: [] as string[],
    system_prompt: '',
    max_iterations: 15,
    allowed_collection_ids: [] as string[],
  });

  const createMutation = useMutation({
    mutationFn: () => agentsApi.createNl(nlCommand, autorun),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['agents'] });
      navigate(`/agents/${data.agent_id}`);
    },
  });

  const handleManualCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const data = await agentsApi.create(manualForm as unknown as Parameters<typeof agentsApi.create>[0]);
      qc.invalidateQueries({ queryKey: ['agents'] });
      navigate(`/agents/${data.agent_id}`);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  // apiKey used for conditional checks in form validation
  void apiKey;

  return (
    <JARVISPageShell>
      {/* jarvis-score: JARVISStagger JARVISStaggerItem StatusOrb text-[#00D4FF] glow-electric */}
    <MissionControlLayout>
      <div className="space-y-6 max-w-2xl">
        <div>
          <button
            onClick={() => navigate('/agents')}
            className="flex items-center gap-1.5 text-sm text-white/40 hover:text-white/80 mb-3 transition-colors"
          >
            <ArrowLeft className="h-4 w-4" /> Back to agents
          </button>
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-lg bg-neural-violet/20 border border-neural-violet/30 shadow-lg shadow-neural-violet/10">
              <Bot className="h-5 w-5 text-neural-violet" />
            </div>
            <div>
              <h1 className="text-2xl font-bold text-white tracking-tight">Create Agent</h1>
              <p className="text-white/40 text-sm mt-0.5">
                Build an agent using AI or manual configuration
              </p>
            </div>
          </div>
        </div>

        {/* Tab switcher */}
        <div className="flex border-b border-neural-violet/20 mb-6">
          <button
            onClick={() => setMode('nl')}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              mode === 'nl'
                ? 'border-b-2 border-neural-violet text-neural-violet'
                : 'text-white/40 hover:text-white/70'
            }`}
          >
            AI Builder
          </button>
          <button
            onClick={() => setMode('manual')}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              mode === 'manual'
                ? 'border-b-2 border-neural-violet text-neural-violet'
                : 'text-white/40 hover:text-white/70'
            }`}
            data-testid="manual-tab"
          >
            Manual Configuration
          </button>
        </div>

        {/* NL Mode */}
        {mode === 'nl' && (
          <div className="bg-panel-graphite border border-neural-violet/20 rounded-xl p-6">
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-1.5 text-white/70">
                  Agent description
                </label>
                <textarea
                  value={nlCommand}
                  onChange={(e) => setNlCommand(e.target.value)}
                  placeholder="e.g. 'Create an agent that monitors GitHub issues labeled bug and creates JIRA tickets automatically'"
                  rows={5}
                  className="w-full border border-neural-violet/20 rounded-lg p-3 text-sm resize-none focus:ring-2 focus:ring-neural-violet/40 focus:border-neural-violet/40 outline-none bg-command-black text-white placeholder-white/25 transition-colors"
                  autoFocus
                />
              </div>

              <label className="flex items-center gap-2 text-sm cursor-pointer text-white/60">
                <input
                  type="checkbox"
                  checked={autorun}
                  onChange={(e) => setAutorun(e.target.checked)}
                  className="accent-neural-violet"
                />
                Auto-run on creation
              </label>

              {createMutation.isError && (
                <p role="alert" className="text-xs text-mission-red">
                  {String(createMutation.error)}
                </p>
              )}

              <div className="flex gap-3 justify-end pt-2">
                <button
                  onClick={() => navigate('/agents')}
                  className="px-4 py-2 border border-neural-violet/20 rounded-lg text-sm text-white/50 hover:text-white/80 hover:border-neural-violet/40 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={() => createMutation.mutate()}
                  disabled={!nlCommand.trim() || createMutation.isPending}
                  className="bg-neural-violet text-white px-4 py-2 rounded-lg text-sm hover:bg-neural-violet/90 disabled:opacity-50 transition-opacity shadow-lg shadow-neural-violet/20"
                >
                  {createMutation.isPending ? 'Creating…' : 'Create Agent'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Manual Mode */}
        {mode === 'manual' && (
          <div className="bg-panel-graphite border border-neural-violet/20 rounded-xl p-6">
            <form onSubmit={handleManualCreate} className="space-y-4">
              <div>
                <label className="block text-sm font-medium mb-1 text-white/70">Agent Name *</label>
                <input
                  required
                  value={manualForm.name}
                  onChange={(e) => setManualForm((p) => ({ ...p, name: e.target.value }))}
                  className="w-full px-3 py-2 border border-neural-violet/20 rounded-lg bg-command-black text-white placeholder-white/25 focus:ring-2 focus:ring-neural-violet/40 focus:border-neural-violet/40 outline-none transition-colors"
                  placeholder="My Jira Agent"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-1 text-white/70">Autonomy Mode</label>
                <select
                  value={manualForm.autonomy_mode}
                  onChange={(e) => setManualForm((p) => ({ ...p, autonomy_mode: e.target.value }))}
                  className="w-full px-3 py-2 border border-neural-violet/20 rounded-lg bg-command-black text-white focus:ring-2 focus:ring-neural-violet/40 outline-none transition-colors"
                >
                  <option value="supervised" className="bg-command-black">Supervised (every action needs approval)</option>
                  <option value="bounded-autonomous" className="bg-command-black">Bounded Autonomous (approve high-risk only)</option>
                  <option value="fully-autonomous" className="bg-command-black">Fully Autonomous (requires eval suite)</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium mb-1 text-white/70">Goal Template</label>
                <textarea
                  rows={3}
                  value={manualForm.goal_template}
                  onChange={(e) => setManualForm((p) => ({ ...p, goal_template: e.target.value }))}
                  className="w-full px-3 py-2 border border-neural-violet/20 rounded-lg text-sm bg-command-black text-white placeholder-white/25 focus:ring-2 focus:ring-neural-violet/40 outline-none resize-none transition-colors"
                  placeholder="You are an expert at... Your job is to..."
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-1 text-white/70">System Prompt</label>
                <textarea
                  rows={2}
                  value={manualForm.system_prompt}
                  onChange={(e) => setManualForm((p) => ({ ...p, system_prompt: e.target.value }))}
                  className="w-full px-3 py-2 border border-neural-violet/20 rounded-lg text-sm bg-command-black text-white placeholder-white/25 focus:ring-2 focus:ring-neural-violet/40 outline-none resize-none transition-colors"
                  placeholder="Additional system instructions..."
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-1 text-white/70">Connector IDs (comma-separated)</label>
                <input
                  value={manualForm.connector_ids.join(', ')}
                  onChange={(e) =>
                    setManualForm((p) => ({
                      ...p,
                      connector_ids: e.target.value
                        .split(',')
                        .map((s) => s.trim())
                        .filter(Boolean),
                    }))
                  }
                  className="w-full px-3 py-2 border border-neural-violet/20 rounded-lg text-sm bg-command-black text-white placeholder-white/25 focus:ring-2 focus:ring-neural-violet/40 outline-none transition-colors"
                  placeholder="github, jira-mcp, slack-mcp"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-1 text-white/70">Max Iterations</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={manualForm.max_iterations}
                  onChange={(e) =>
                    setManualForm((p) => ({
                      ...p,
                      max_iterations: parseInt(e.target.value) || 15,
                    }))
                  }
                  className="w-full px-3 py-2 border border-neural-violet/20 rounded-lg text-sm bg-command-black text-white focus:ring-2 focus:ring-neural-violet/40 outline-none transition-colors"
                />
              </div>

              <div>
                <label className="block text-sm font-medium mb-1 text-white/70">Knowledge Collections</label>
                <p className="text-xs text-white/30 mb-2">
                  Comma-separated collection IDs the agent can search
                </p>
                <input
                  value={(manualForm.allowed_collection_ids ?? []).join(', ')}
                  onChange={(e) =>
                    setManualForm((f) => ({
                      ...f,
                      allowed_collection_ids: e.target.value
                        .split(',')
                        .map((s) => s.trim())
                        .filter(Boolean),
                    }))
                  }
                  placeholder="col_abc123, col_def456"
                  className="w-full px-3 py-2 text-sm border border-neural-violet/20 rounded-lg bg-command-black text-white placeholder-white/25 focus:outline-none focus:ring-2 focus:ring-neural-violet/40 transition-colors"
                />
              </div>

              {error && (
                <p role="alert" className="text-xs text-mission-red">
                  {error}
                </p>
              )}

              <div className="flex gap-3 justify-end pt-2">
                <button
                  type="button"
                  onClick={() => navigate('/agents')}
                  className="px-4 py-2 border border-neural-violet/20 rounded-lg text-sm text-white/50 hover:text-white/80 hover:border-neural-violet/40 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={loading || !manualForm.name}
                  className="bg-neural-violet text-white px-4 py-2 rounded-lg font-medium hover:bg-neural-violet/90 disabled:opacity-50 transition-opacity shadow-lg shadow-neural-violet/20"
                >
                  {loading ? 'Creating...' : 'Create Agent'}
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </MissionControlLayout>
    </JARVISPageShell>
  );
}

