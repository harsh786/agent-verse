import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { a2aApi, type A2ATask } from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { StatusBadge } from '@/components/ui/StatusBadge';

export function A2APage() {
  const [taskId, setTaskId] = useState('');
  const [tasks, setTasks] = useState<A2ATask[]>([]);

  const { data: card, isLoading } = useQuery({
    queryKey: ['a2a-card'],
    queryFn: () => a2aApi.agentCard(),
  });

  const lookupMutation = useMutation({
    mutationFn: (id: string) => a2aApi.getTask(id),
    onSuccess: (task) =>
      setTasks((prev) => [task, ...prev.filter((t) => t.task_id !== task.task_id)]),
    onError: () => toast({ kind: 'error', message: 'Task not found.' }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">A2A Observability</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Agent-to-agent capability card and task status (read-only)
        </p>
      </div>

      {/* Agent card */}
      <div className="bg-card border border-border rounded-xl p-5">
        {isLoading ? (
          <p className="text-sm text-muted-foreground">Loading agent card…</p>
        ) : card ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="font-semibold">{card.name}</h2>
                <p className="text-xs text-muted-foreground">
                  {card.agent_id} · v{card.version}
                </p>
              </div>
              <code className="text-xs bg-muted rounded px-2 py-1">
                {card.authentication.scheme}
              </code>
            </div>
            <p className="text-sm text-muted-foreground">{card.description}</p>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Capabilities</p>
              <div className="flex flex-wrap gap-1.5">
                {card.capabilities.map((c) => (
                  <code key={c} className="text-xs bg-muted rounded px-2 py-0.5">
                    {c}
                  </code>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs text-muted-foreground mb-1">Task types</p>
              <div className="flex flex-wrap gap-1.5">
                {card.supported_task_types.map((c) => (
                  <code key={c} className="text-xs bg-muted rounded px-2 py-0.5">
                    {c}
                  </code>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Agent card unavailable.</p>
        )}
      </div>

      {/* Task lookup */}
      <div className="bg-card border border-border rounded-xl p-4">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (taskId.trim()) lookupMutation.mutate(taskId.trim());
          }}
        >
          <input
            value={taskId}
            onChange={(e) => setTaskId(e.target.value)}
            placeholder="A2A task id"
            aria-label="Task id"
            className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-background"
          />
          <button
            type="submit"
            disabled={!taskId.trim() || lookupMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-2 bg-primary text-primary-foreground rounded-md text-sm disabled:opacity-50"
          >
            <Search className="h-4 w-4" /> Look up
          </button>
        </form>
      </div>

      {tasks.length > 0 && (
        <div className="space-y-3">
          {tasks.map((t) => (
            <div key={t.task_id} className="bg-card border border-border rounded-xl p-4">
              <div className="flex items-center justify-between mb-1">
                <code className="text-xs text-muted-foreground">{t.task_id}</code>
                <StatusBadge status={t.status} />
              </div>
              <p className="text-sm">{t.goal}</p>
              {t.result && <p className="text-xs text-muted-foreground mt-1">{t.result}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
