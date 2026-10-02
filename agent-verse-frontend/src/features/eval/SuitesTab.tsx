import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, FlaskConical, Pencil, Plus, Trash2, Upload, X } from 'lucide-react';
import {
  evalSuitesApi,
  goldenTaskHasChecks,
  type EvalSuiteResult,
  type EvalSuiteTaskResult,
  type GoldenTask,
  type GoldenTaskInput,
} from '@/lib/api/client';
import { toast } from '@/stores/toast';
import { ConfirmModal } from '@/components/ui/ConfirmModal';

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

// ── Golden task form ─────────────────────────────────────────────────────────

interface GoldenTaskForm {
  goal: string;
  expected_output_contains: string;
  expected_tools: string;
  forbidden_tools: string;
  expected_output: string;
  min_score: string;
}

const EMPTY_TASK_FORM: GoldenTaskForm = {
  goal: '', expected_output_contains: '', expected_tools: '', forbidden_tools: '', expected_output: '', min_score: '0.8',
};

function splitList(value: string): string[] {
  return value.split(',').map((t) => t.trim()).filter(Boolean);
}

function taskFormToInput(form: GoldenTaskForm): GoldenTaskInput {
  return {
    goal: form.goal.trim(),
    expected_tools: splitList(form.expected_tools),
    forbidden_tools: splitList(form.forbidden_tools),
    expected_output_contains: splitList(form.expected_output_contains),
    expected_output: form.expected_output.trim() || undefined,
    min_score: form.min_score ? Number(form.min_score) : undefined,
  };
}

function taskToForm(task: GoldenTask): GoldenTaskForm {
  return {
    goal: task.goal,
    expected_output_contains: (task.expected_output_contains ?? []).join(', '),
    expected_tools: (task.expected_tools ?? []).join(', '),
    forbidden_tools: (task.forbidden_tools ?? []).join(', '),
    expected_output: task.expected_output ?? '',
    min_score: String(task.min_score ?? 0.8),
  };
}

// ── Per-task outcome of a run ────────────────────────────────────────────────

const TERMINAL_LABEL: Record<string, string> = {
  goal_complete: 'completed',
  goal_failed: 'goal failed',
  goal_cancelled: 'goal cancelled',
  goal_rejected: 'goal rejected',
};

function TaskOutcome({ t }: { t: EvalSuiteTaskResult }) {
  const tone = t.passed
    ? 'border-emerald-500/30 text-emerald-500'
    : t.status && t.status !== 'scored'
      ? 'border-amber-500/30 text-amber-500'
      : 'border-red-500/30 text-red-500';
  const outcome = t.status === 'timeout'
    ? 'timed out'
    : t.status === 'error'
      ? 'error'
      : t.status === 'invalid'
        ? 'invalid (no checks)'
        : TERMINAL_LABEL[t.terminal_event ?? ''] ?? (t.terminal_event || 'no outcome');
  return (
    <span
      data-testid={`task-outcome-${t.task_id}`}
      title={t.failure_reasons?.join('; ')}
      className={`text-[10px] px-1.5 py-0.5 rounded border ${tone}`}
    >
      {t.task_id}: {t.passed ? 'pass' : 'fail'} · {outcome}
      {typeof t.score === 'number' && ` · score ${t.score.toFixed(2)}`}
      {t.judge?.llm_judged && ' (judge)'}
    </span>
  );
}

// ── Golden dataset (current version) of one suite ────────────────────────────

function GoldenTaskList({
  suiteId, onEdit, onDelete,
}: {
  suiteId: string;
  onEdit: (task: GoldenTask) => void;
  onDelete: (task: GoldenTask) => void;
}) {
  const { data: detail } = useQuery({
    queryKey: ['eval-suite', suiteId],
    queryFn: () => evalSuitesApi.getSuite(suiteId),
  });
  const tasks: GoldenTask[] = Array.isArray(detail?.tasks) ? detail.tasks : [];
  if (!detail || Array.isArray(detail)) return null;
  return (
    <div className="border-t border-border p-4" data-testid={`golden-tasks-${suiteId}`}>
      <h4 className="text-xs font-semibold text-muted-foreground mb-2">
        Golden Tasks · dataset v{detail.dataset_version ?? 0}
      </h4>
      {tasks.length === 0 ? (
        <p className="text-xs text-muted-foreground/60">No golden tasks in this version.</p>
      ) : (
        <ul className="space-y-1">
          {tasks.map((task) => (
            <li key={task.task_id} className="flex items-center gap-2 text-xs">
              <span className="flex-1 text-foreground truncate" title={task.goal}>{task.goal}</span>
              <span className="text-muted-foreground/60">rev v{task.revision ?? '?'}</span>
              <button
                aria-label={`Edit task: ${task.goal}`}
                onClick={() => onEdit(task)}
                className="p-1 rounded text-muted-foreground hover:text-foreground"
              >
                <Pencil className="h-3 w-3" />
              </button>
              <button
                aria-label={`Delete task: ${task.goal}`}
                onClick={() => onDelete(task)}
                className="p-1 rounded text-muted-foreground hover:text-red-600"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {detail.tasks_truncated && (
        <p className="text-[10px] text-muted-foreground/60 mt-1">
          Showing the first {tasks.length} of {detail.task_count} tasks.
        </p>
      )}
    </div>
  );
}

// ── Tab ──────────────────────────────────────────────────────────────────────

export function SuitesTab({ apiKey }: { apiKey: string }) {
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [suiteName, setSuiteName] = useState('');
  const [suiteDesc, setSuiteDesc] = useState('');
  const [activeSuiteId, setActiveSuiteId] = useState<string | null>(null);
  const [showAddTask, setShowAddTask] = useState(false);
  const [editingTask, setEditingTask] = useState<GoldenTask | null>(null);
  const [deleteSuiteId, setDeleteSuiteId] = useState<string | null>(null);
  const [deleteTask, setDeleteTask] = useState<GoldenTask | null>(null);
  const [taskForm, setTaskForm] = useState<GoldenTaskForm>(EMPTY_TASK_FORM);
  const importInput = useRef<HTMLInputElement>(null);
  const taskInput = taskFormToInput(taskForm);
  const taskHasChecks = goldenTaskHasChecks(taskInput);

  const invalidateSuite = (suiteId: string | null) => {
    qc.invalidateQueries({ queryKey: ['eval-suites'] });
    if (suiteId) qc.invalidateQueries({ queryKey: ['eval-suite', suiteId] });
  };

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

  // Per-suite results map — prevents cross-contamination when switching suites
  const [suiteResultsMap, setSuiteResultsMap] = useState<Map<string, EvalSuiteResult[]>>(new Map());

  const { data: fetchedSuiteResults } = useQuery({
    queryKey: ['suite-results', activeSuiteId],
    queryFn: () => evalSuitesApi.getSuiteResults(activeSuiteId!),
    enabled: !!activeSuiteId,
  });

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

  const closeTaskModal = () => {
    setShowAddTask(false);
    setEditingTask(null);
  };

  const saveTaskMutation = useMutation({
    mutationFn: async (): Promise<void> => {
      if (editingTask) await evalSuitesApi.updateTask(activeSuiteId!, editingTask.task_id, taskInput);
      else await evalSuitesApi.addTask(activeSuiteId!, taskInput);
    },
    onSuccess: () => {
      toast({ kind: 'success', message: editingTask ? 'Task updated (new dataset version)' : 'Task added' });
      closeTaskModal();
      setTaskForm(EMPTY_TASK_FORM);
      invalidateSuite(activeSuiteId);
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const deleteTaskMutation = useMutation({
    mutationFn: (task: GoldenTask) => evalSuitesApi.deleteTask(activeSuiteId!, task.task_id),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Task removed (new dataset version)' });
      setDeleteTask(null);
      invalidateSuite(activeSuiteId);
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const runMutation = useMutation({
    mutationFn: (id: string) => evalSuitesApi.runSuite(id),
    onSuccess: (_, suiteId) => {
      qc.invalidateQueries({ queryKey: ['suite-results', suiteId] });
    },
  });

  const exportDataset = async (suiteId: string) => {
    try {
      const doc = await evalSuitesApi.exportDataset(suiteId);
      const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${suiteId}-v${doc.dataset_version}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast({ kind: 'error', message: String(e) });
    }
  };

  const importDataset = async (file: File) => {
    if (!activeSuiteId) return;
    try {
      const parsed = JSON.parse(await file.text()) as { tasks?: GoldenTaskInput[] } | GoldenTaskInput[];
      const tasks = Array.isArray(parsed) ? parsed : parsed.tasks ?? [];
      if (tasks.length === 0) throw new Error('The file has no tasks');
      const res = await evalSuitesApi.importDataset(activeSuiteId, tasks);
      toast({ kind: 'success', message: `Imported ${res.imported} tasks as dataset v${res.dataset_version}` });
      invalidateSuite(activeSuiteId);
    } catch (e) {
      toast({ kind: 'error', message: `Import failed: ${String(e)}` });
    }
  };

  const typedSuites = suites as Array<{
    suite_id: string; name?: string; task_count?: number; created_at?: string;
    description?: string; dataset_version?: number;
  }>;

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
                    {typeof suite.dataset_version === 'number' && ` · dataset v${suite.dataset_version}`}
                    {suite.created_at && ` · ${new Date(suite.created_at).toLocaleDateString()}`}
                    {suite.description && ` · ${suite.description}`}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={(e) => { e.stopPropagation(); setActiveSuiteId(suite.suite_id); setEditingTask(null); setTaskForm(EMPTY_TASK_FORM); setShowAddTask(true); }}
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
                    onClick={(e) => { e.stopPropagation(); void exportDataset(suite.suite_id); }}
                    className="p-1 rounded text-muted-foreground hover:text-foreground"
                    aria-label={`Export dataset: ${suite.name ?? suite.suite_id}`}
                  >
                    <Download className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setActiveSuiteId(suite.suite_id); importInput.current?.click(); }}
                    className="p-1 rounded text-muted-foreground hover:text-foreground"
                    aria-label={`Import dataset: ${suite.name ?? suite.suite_id}`}
                  >
                    <Upload className="h-3.5 w-3.5" />
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

              {activeSuiteId === suite.suite_id && (
                <GoldenTaskList
                  suiteId={suite.suite_id}
                  onEdit={(task) => { setEditingTask(task); setTaskForm(taskToForm(task)); setShowAddTask(true); }}
                  onDelete={(task) => setDeleteTask(task)}
                />
              )}

              {activeSuiteId === suite.suite_id && (suiteResultsMap.get(suite.suite_id)?.length ?? 0) > 0 && (
                <div className="border-t border-border p-4">
                  <h4 className="text-xs font-semibold text-muted-foreground mb-2">Recent Runs</h4>
                  <div className="space-y-1.5">
                    {(suiteResultsMap.get(suite.suite_id) ?? []).slice(-5).map((r, i) => {
                      const tasks = r.task_results ?? [];
                      const unscored = tasks.filter((t) => t.status && t.status !== 'scored');
                      return (
                        <div key={r.run_id ?? i} className="space-y-1">
                          <div className="flex items-center gap-3 text-xs">
                            <span className="text-muted-foreground/60">#{i + 1}</span>
                            {r.dataset_version != null && (
                              <span className="text-muted-foreground/60" data-testid={`run-version-${r.run_id}`}>
                                dataset v{r.dataset_version}
                              </span>
                            )}
                            <div className="flex-1 bg-muted rounded-full h-1.5">
                              <div
                                className="bg-emerald-500 h-1.5 rounded-full"
                                style={{ width: `${((r.passed ?? 0) / Math.max((r.passed ?? 0) + (r.failed ?? 0), 1)) * 100}%` }}
                              />
                            </div>
                            <span className="text-muted-foreground">{r.passed ?? 0}/{(r.passed ?? 0) + (r.failed ?? 0)} pass</span>
                            {unscored.length > 0 && (
                              <span className="text-amber-500">{unscored.length} not scored</span>
                            )}
                          </div>
                          {tasks.length > 0 && (
                            <div className="flex flex-wrap gap-1 pl-6">
                              {tasks.map((t) => <TaskOutcome key={t.task_id} t={t} />)}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}

      <input
        ref={importInput}
        type="file"
        accept="application/json,.json"
        aria-label="Import dataset file"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void importDataset(file);
          e.target.value = '';
        }}
      />

      {/* Add / edit task modal */}
      {showAddTask && activeSuiteId && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={closeTaskModal}>
          <Card className="w-full max-w-md p-6 space-y-4" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-foreground">{editingTask ? 'Edit Golden Task' : 'Add Golden Task'}</h3>
              <button onClick={closeTaskModal} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            {editingTask && (
              <p className="text-xs text-muted-foreground">
                Saving creates a new dataset version; runs of earlier versions keep the old task.
              </p>
            )}
            {[
              { label: 'Goal', key: 'goal', placeholder: 'What should the agent do?' },
              { label: 'Expected output contains', key: 'expected_output_contains', placeholder: 'Expected substring in output' },
              { label: 'Expected tools (comma-separated)', key: 'expected_tools', placeholder: 'github:list_issues, slack:send_message' },
              { label: 'Forbidden tools', key: 'forbidden_tools', placeholder: 'shell:execute, db:delete' },
              { label: 'Reference answer (judged)', key: 'expected_output', placeholder: 'What a correct answer says' },
              { label: 'Min score', key: 'min_score', placeholder: '0.8' },
            ].map(({ label, key, placeholder }) => (
              <div key={key}>
                <label className="text-xs text-muted-foreground block mb-1">{label}</label>
                <input
                  aria-label={label}
                  value={taskForm[key as keyof GoldenTaskForm]}
                  onChange={(e) => setTaskForm((f) => ({ ...f, [key]: e.target.value }))}
                  placeholder={placeholder}
                  className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm text-foreground outline-none"
                />
              </div>
            ))}
            {taskForm.goal.trim() && !taskHasChecks && (
              <p role="alert" className="text-xs text-amber-500">
                Add at least one check — an expected tool, a forbidden tool, an expected phrase or a
                reference answer. A task without checks cannot measure the agent.
              </p>
            )}
            <div className="flex gap-2 justify-end">
              <button onClick={closeTaskModal} className="px-4 py-2 border border-border text-muted-foreground text-sm rounded-lg">
                Cancel
              </button>
              <button
                onClick={() => saveTaskMutation.mutate()}
                disabled={!taskForm.goal.trim() || !taskHasChecks || saveTaskMutation.isPending}
                className="px-4 py-2 bg-indigo-600 text-foreground text-sm rounded-lg disabled:opacity-50"
              >
                {saveTaskMutation.isPending ? 'Saving…' : editingTask ? 'Save Task' : 'Add Task'}
              </button>
            </div>
          </Card>
        </div>
      )}
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
      <ConfirmModal
        open={!!deleteTask}
        title="Remove golden task?"
        description="The task is removed from a new dataset version. Earlier versions, and the runs that used them, keep it."
        confirmLabel="Remove Task"
        variant="danger"
        isLoading={deleteTaskMutation.isPending}
        onConfirm={() => { if (deleteTask) deleteTaskMutation.mutate(deleteTask); }}
        onCancel={() => setDeleteTask(null)}
      />
    </div>
  );
}
