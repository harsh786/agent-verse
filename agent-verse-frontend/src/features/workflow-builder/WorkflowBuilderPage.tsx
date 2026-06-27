import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Play, Download, Plus, Trash2 } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { API_BASE } from '@/lib/api/client';

interface WorkflowNode {
  id: string;
  type: 'start' | 'step' | 'decision' | 'end';
  label: string;
  tool?: string;
  args?: string;
  condition?: string;
}

function NodeCard({ node, onDelete, onEdit }: {
  node: WorkflowNode;
  onDelete: () => void;
  onEdit: (updates: Partial<WorkflowNode>) => void;
}) {
  const colors: Record<string, string> = {
    start: 'bg-green-100 border-green-400',
    step: 'bg-blue-100 border-blue-400',
    decision: 'bg-yellow-100 border-yellow-400',
    end: 'bg-red-100 border-red-400',
  };
  return (
    <div className={`border-2 rounded-xl p-3 ${colors[node.type]} relative min-w-[180px]`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-bold uppercase text-muted-foreground">{node.type}</span>
        {node.type !== 'start' && node.type !== 'end' && (
          <button onClick={onDelete} className="text-red-500 hover:text-red-700">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <input
        value={node.label}
        onChange={e => onEdit({ label: e.target.value })}
        className="text-sm font-medium w-full bg-transparent border-b border-current/30 outline-none"
        placeholder="Step description..."
      />
      {node.type === 'step' && (
        <input
          value={node.tool || ''}
          onChange={e => onEdit({ tool: e.target.value })}
          className="text-xs text-muted-foreground w-full bg-transparent mt-1 outline-none"
          placeholder="Tool: e.g. jira.search_issues"
        />
      )}
    </div>
  );
}

export function WorkflowBuilderPage() {
  const apiKey = useAuthStore(s => s.apiKey);
  const navigate = useNavigate();
  const [nodes, setNodes] = useState<WorkflowNode[]>([
    { id: 'start', type: 'start', label: 'Start' },
    { id: 'end', type: 'end', label: 'End' },
  ]);
  const [goalInput, setGoalInput] = useState('');
  const [generating, setGenerating] = useState(false);
  const [running, setRunning] = useState(false);

  const addNode = (type: WorkflowNode['type']) => {
    const id = `node_${Date.now()}`;
    setNodes(prev => {
      const endIdx = prev.findIndex(n => n.id === 'end');
      const newNodes = [...prev];
      newNodes.splice(endIdx, 0, { id, type, label: `New ${type}`, tool: '' });
      return newNodes;
    });
  };

  const deleteNode = (id: string) => {
    setNodes(prev => prev.filter(n => n.id !== id));
  };

  const editNode = (id: string, updates: Partial<WorkflowNode>) => {
    setNodes(prev => prev.map(n => n.id === id ? { ...n, ...updates } : n));
  };

  const generateFromGoal = async () => {
    if (!goalInput.trim()) return;
    setGenerating(true);
    try {
      const res = await fetch(`${API_BASE}/goals`, {
        method: 'POST',
        headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal: goalInput, dry_run: true }),
      });
      if (res.ok) {
        setNodes([
          { id: 'start', type: 'start', label: 'Start' },
          { id: 'step_1', type: 'step', label: goalInput.slice(0, 60), tool: '' },
          { id: 'end', type: 'end', label: 'End' },
        ]);
      }
    } finally {
      setGenerating(false);
    }
  };

  const runWorkflow = async () => {
    const stepsText = nodes
      .filter(n => n.type === 'step')
      .map(n => n.tool ? `${n.label} (${n.tool})` : n.label)
      .join(', ');
    const goal = `Execute workflow: ${stepsText}`;
    setRunning(true);
    try {
      const res = await fetch(`${API_BASE}/goals`, {
        method: 'POST',
        headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({ goal, dry_run: false }),
      });
      if (res.ok) {
        const data = await res.json();
        navigate(`/goals/${data.goal_id}`);
      }
    } finally {
      setRunning(false);
    }
  };

  const exportWorkflow = () => {
    const manifest = {
      name: 'Custom Workflow',
      version: '1.0.0',
      description: goalInput || 'Manually built workflow',
      steps: nodes.filter(n => n.type === 'step').map(n => ({
        id: n.id, description: n.label, tool: n.tool,
      })),
    };
    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'workflow.json';
    a.click();
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Workflow Builder</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Build multi-step agent workflows visually
        </p>
      </div>

      {/* Goal input for auto-generation */}
      <div className="bg-card border border-border rounded-xl p-4 flex gap-3">
        <input
          value={goalInput}
          onChange={e => setGoalInput(e.target.value)}
          placeholder="Describe your goal to auto-generate a workflow..."
          className="flex-1 border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary"
        />
        <button
          onClick={generateFromGoal}
          disabled={!goalInput.trim() || generating}
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm disabled:opacity-50"
        >
          {generating ? 'Generating…' : '✨ Auto-Generate'}
        </button>
      </div>

      {/* Toolbar */}
      <div className="flex gap-2 flex-wrap">
        <button onClick={() => addNode('step')}
          className="flex items-center gap-1.5 border border-border px-3 py-1.5 rounded-lg text-sm hover:bg-accent">
          <Plus className="h-3.5 w-3.5" /> Add Step
        </button>
        <button onClick={() => addNode('decision')}
          className="flex items-center gap-1.5 border border-border px-3 py-1.5 rounded-lg text-sm hover:bg-accent">
          <Plus className="h-3.5 w-3.5" /> Add Decision
        </button>
        <div className="ml-auto flex gap-2">
          <button onClick={exportWorkflow}
            className="flex items-center gap-1.5 border border-border px-3 py-1.5 rounded-lg text-sm hover:bg-accent">
            <Download className="h-3.5 w-3.5" /> Export
          </button>
          <button onClick={runWorkflow} disabled={running}
            className="flex items-center gap-2 bg-green-600 text-white px-4 py-1.5 rounded-lg text-sm hover:bg-green-700 disabled:opacity-50">
            <Play className="h-3.5 w-3.5" /> {running ? 'Running…' : 'Run Workflow'}
          </button>
        </div>
      </div>

      {/* Workflow canvas */}
      <div className="bg-card border border-border rounded-xl p-6">
        <div className="flex flex-col items-center gap-3">
          {nodes.map((node, i) => (
            <div key={node.id} className="flex flex-col items-center gap-1">
              <NodeCard
                node={node}
                onDelete={() => deleteNode(node.id)}
                onEdit={updates => editNode(node.id, updates)}
              />
              {i < nodes.length - 1 && (
                <div className="w-px h-6 bg-border" />
              )}
            </div>
          ))}
        </div>
        {nodes.filter(n => n.type === 'step').length === 0 && (
          <div className="text-center text-sm text-muted-foreground mt-4">
            Add steps using the toolbar above, or describe a goal to auto-generate.
          </div>
        )}
      </div>
    </div>
  );
}
