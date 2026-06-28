import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { agentsApi } from '@/lib/api/client';

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
    <div className="space-y-6 max-w-2xl">
      <div>
        <button
          onClick={() => navigate('/agents')}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground mb-3 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> Back to agents
        </button>
        <h1 className="text-2xl font-bold">Create Agent</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Build an agent using AI or manual configuration
        </p>
      </div>

      {/* Tab switcher */}
      <div className="flex border-b mb-6">
        <button
          onClick={() => setMode('nl')}
          className={`px-4 py-2 text-sm font-medium ${mode === 'nl' ? 'border-b-2 border-primary text-primary' : 'text-muted-foreground hover:text-foreground'}`}
        >
          AI Builder
        </button>
        <button
          onClick={() => setMode('manual')}
          className={`px-4 py-2 text-sm font-medium ${mode === 'manual' ? 'border-b-2 border-primary text-primary' : 'text-muted-foreground hover:text-foreground'}`}
          data-testid="manual-tab"
        >
          Manual Configuration
        </button>
      </div>

      {/* NL Mode */}
      {mode === 'nl' && (
        <div className="bg-card border border-border rounded-xl p-6">
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1.5">
                Agent description
              </label>
              <textarea
                value={nlCommand}
                onChange={(e) => setNlCommand(e.target.value)}
                placeholder="e.g. 'Create an agent that monitors GitHub issues labeled bug and creates JIRA tickets automatically'"
                rows={5}
                className="w-full border border-input rounded-lg p-3 text-sm resize-none focus:ring-2 focus:ring-primary outline-none bg-background"
                autoFocus
              />
            </div>

            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={autorun}
                onChange={(e) => setAutorun(e.target.checked)}
                className="accent-primary"
              />
              Auto-run on creation
            </label>

            {createMutation.isError && (
              <p role="alert" className="text-xs text-red-600">
                {String(createMutation.error)}
              </p>
            )}

            <div className="flex gap-3 justify-end pt-2">
              <button
                onClick={() => navigate('/agents')}
                className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => createMutation.mutate()}
                disabled={!nlCommand.trim() || createMutation.isPending}
                className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {createMutation.isPending ? 'Creating…' : 'Create Agent'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Manual Mode */}
      {mode === 'manual' && (
        <div className="bg-card border border-border rounded-xl p-6">
          <form onSubmit={handleManualCreate} className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1">Agent Name *</label>
              <input
                required
              value={manualForm.name}
              onChange={(e) => setManualForm((p) => ({ ...p, name: e.target.value }))}
              className="w-full px-3 py-2 border border-input rounded bg-background focus:ring-2 focus:ring-primary outline-none"
              placeholder="My Jira Agent"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Autonomy Mode</label>
              <select
              value={manualForm.autonomy_mode}
              onChange={(e) => setManualForm((p) => ({ ...p, autonomy_mode: e.target.value }))}
              className="w-full px-3 py-2 border border-input rounded bg-background focus:ring-2 focus:ring-primary outline-none"
              >
                <option value="supervised">Supervised (every action needs approval)</option>
                <option value="bounded-autonomous">Bounded Autonomous (approve high-risk only)</option>
                <option value="fully-autonomous">Fully Autonomous (requires eval suite)</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Goal Template</label>
              <textarea
                rows={3}
                value={manualForm.goal_template}
                onChange={(e) => setManualForm((p) => ({ ...p, goal_template: e.target.value }))}
                className="w-full px-3 py-2 border border-input rounded text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
                placeholder="You are an expert at... Your job is to..."
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">System Prompt</label>
              <textarea
                rows={2}
                value={manualForm.system_prompt}
                onChange={(e) => setManualForm((p) => ({ ...p, system_prompt: e.target.value }))}
                className="w-full px-3 py-2 border border-input rounded text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
                placeholder="Additional system instructions..."
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Connector IDs (comma-separated)</label>
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
                className="w-full px-3 py-2 border border-input rounded text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
                placeholder="github, jira-mcp, slack-mcp"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Max Iterations</label>
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
                className="w-full px-3 py-2 border border-input rounded text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
              />
            </div>

            {error && (
              <p role="alert" className="text-xs text-red-600">
                {error}
              </p>
            )}

            <div className="flex gap-3 justify-end pt-2">
              <button
                type="button"
                onClick={() => navigate('/agents')}
                className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={loading || !manualForm.name}
                className="bg-primary text-primary-foreground px-4 py-2 rounded-lg font-medium hover:opacity-90 disabled:opacity-50"
              >
                {loading ? 'Creating...' : 'Create Agent'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
