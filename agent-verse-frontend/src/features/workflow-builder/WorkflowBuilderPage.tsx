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
  position?: { x: number; y: number };
}

interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
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
  const [edges, setEdges] = useState<WorkflowEdge[]>([]);
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
      // Submit as dry_run to get the plan without executing
      const res = await fetch(`${API_BASE}/goals`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify({ goal: goalInput, dry_run: true, autonomy_mode: 'bounded-autonomous' }),
      });

      if (!res.ok) throw new Error(`API error: ${res.status}`);
      const data = await res.json();

      // Convert the returned plan steps into workflow nodes
      const steps: string[] = data.plan?.steps || data.structured_plan?.steps || [];

      if (steps.length > 0) {
        // Build nodes from actual plan
        const newNodes: WorkflowNode[] = [
          { id: 'start', type: 'start', label: 'Start', position: { x: 250, y: 50 } },
          ...steps.slice(0, 20).map((step: string, i: number) => ({
            id: `step_${i + 1}`,
            type: 'step' as const,
            label: step.length > 80 ? step.slice(0, 77) + '...' : step,
            tool: '',
            position: { x: 250, y: 150 + i * 100 },
          })),
          { id: 'end', type: 'end', label: 'End', position: { x: 250, y: 150 + steps.length * 100 } },
        ];

        // Build edges: linear flow
        const newEdges: WorkflowEdge[] = newNodes.slice(0, -1).map((node, i) => ({
          id: `e_${i}`,
          source: node.id,
          target: newNodes[i + 1].id,
        }));

        setNodes(newNodes);
        setEdges(newEdges);
      } else {
        // Fallback: poll for plan via the goal detail endpoint
        const goalId = data.goal_id;
        if (goalId) {
          // Wait briefly then fetch goal detail which includes execution context
          await new Promise(r => setTimeout(r, 2000));
          const evtRes = await fetch(`${API_BASE}/goals/${goalId}`, {
            headers: { 'X-API-Key': apiKey },
          });
          if (evtRes.ok) {
            const goalData = await evtRes.json();
            // Plan may be in execution_context.plan or structured_plan
            const planSteps: string[] =
              goalData?.execution_context?.plan?.steps ??
              goalData?.structured_plan?.steps ??
              [];
            setNodes([
              { id: 'start', type: 'start', label: 'Start' },
              ...(planSteps.length > 0
                ? planSteps.map((s: string, i: number) => ({
                    id: `step_${i}`,
                    type: 'step' as const,
                    label: s.slice(0, 60),
                    tool: '',
                  }))
                : [{ id: 'step_1', type: 'step' as const, label: goalInput.slice(0, 60), tool: '' }]
              ),
              { id: 'end', type: 'end', label: 'End' },
            ]);
          }
        }
      }
    } catch (err) {
      console.error('Workflow generation failed:', err);
      // Minimal fallback on complete failure
      setNodes([
        { id: 'start', type: 'start', label: 'Start' },
        { id: 'step_1', type: 'step', label: goalInput.slice(0, 60), tool: '' },
        { id: 'end', type: 'end', label: 'End' },
      ]);
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
      edges: edges.map(e => ({ id: e.id, source: e.source, target: e.target })),
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
