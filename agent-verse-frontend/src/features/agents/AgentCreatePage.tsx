import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

async function createAgentNL(
  apiKey: string,
  command: string,
  autorun: boolean
): Promise<{ agent_id: string; name: string }> {
  const res = await fetch(`${API_BASE}/agents/create`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
    body: JSON.stringify({ command, autorun }),
  });
  if (!res.ok) throw new Error(`Failed to create agent: ${res.statusText}`);
  return res.json();
}

export function AgentCreatePage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [nlCommand, setNlCommand] = useState('');
  const [autorun, setAutorun] = useState(false);

  const createMutation = useMutation({
    mutationFn: () => createAgentNL(apiKey, nlCommand, autorun),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agents'] });
      navigate('/agents');
    },
  });

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
          Describe your agent in natural language
        </p>
      </div>

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
    </div>
  );
}
