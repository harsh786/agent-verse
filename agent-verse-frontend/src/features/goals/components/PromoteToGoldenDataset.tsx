/**
 * "Promote to golden dataset" (a10-F235-01): turn a completed goal into a golden
 * task of one of the tenant's eval suites. The goal's verified answer, the tools
 * it called and the sources it cited become the task's expectation, added as a
 * new dataset version of the chosen suite.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Award, Loader2, X } from "lucide-react";
import { evalSuitesApi, type PromoteGoalResult } from "@/lib/api/client";
import { toast } from "@/stores/toast";

interface Props {
  goalId: string;
  /** Only a completed, executed goal can be promoted. */
  status: string;
  dryRun?: boolean;
}

function splitTags(raw: string): string[] {
  return raw
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

export function PromoteToGoldenDataset({ goalId, status, dryRun }: Props) {
  const [open, setOpen] = useState(false);
  const [suiteId, setSuiteId] = useState("");
  const [tags, setTags] = useState("");
  const [context, setContext] = useState("");
  const [includeTools, setIncludeTools] = useState(true);
  const [includeCitations, setIncludeCitations] = useState(true);
  const [done, setDone] = useState<PromoteGoalResult | null>(null);
  const qc = useQueryClient();

  const suites = useQuery({
    queryKey: ["eval-suites"],
    queryFn: () => evalSuitesApi.listSuites(),
    enabled: open,
  });

  const promote = useMutation({
    mutationFn: () =>
      evalSuitesApi.promoteGoal(suiteId, goalId, {
        tags: splitTags(tags),
        context: context.trim() || undefined,
        include_tools: includeTools,
        include_citations: includeCitations,
      }),
    onSuccess: (result) => {
      setDone(result);
      void qc.invalidateQueries({ queryKey: ["eval-suites"] });
      toast({
        kind: "success",
        message: `Promoted to golden dataset (version ${result.dataset_version})`,
      });
    },
  });

  if (status !== "complete" || dryRun) return null;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => {
          setOpen(true);
          setDone(null);
          promote.reset();
        }}
        className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-border rounded-lg hover:bg-accent transition-colors"
      >
        <Award className="h-4 w-4" aria-hidden="true" /> Promote to golden dataset
      </button>
    );
  }

  const suiteName = (id: string) =>
    suites.data?.find((s) => s.suite_id === id)?.name ?? id;

  return (
    <div
      role="dialog"
      aria-label="Promote to golden dataset"
      className="w-full rounded-xl border border-border bg-card p-4 space-y-3"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Promote to golden dataset</h2>
        <button
          type="button"
          aria-label="Close"
          onClick={() => setOpen(false)}
          className="p-1 rounded hover:bg-muted"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <p className="text-xs text-muted-foreground">
        The goal's verified answer becomes the reference; the tools it called and the
        sources it cited can be required too. The task is added as a new dataset
        version of the suite.
      </p>

      {done ? (
        <p role="status" className="text-sm text-green-700 dark:text-green-400">
          Added to {suiteName(done.suite_id)} as task <code>{done.task_id}</code> (dataset
          version {done.dataset_version}).
        </p>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (suiteId) promote.mutate();
          }}
        >
          <label className="block text-xs font-medium">
            Eval suite
            {suites.isLoading ? (
              <span className="ml-2 text-muted-foreground">Loading suites…</span>
            ) : suites.isError ? (
              <span role="alert" className="ml-2 text-destructive">
                Could not load your eval suites.
              </span>
            ) : (suites.data ?? []).length === 0 ? (
              <span className="ml-2 text-muted-foreground">
                No eval suites yet — create one under Evaluations first.
              </span>
            ) : (
              <select
                value={suiteId}
                onChange={(e) => setSuiteId(e.target.value)}
                className="mt-1 block w-full rounded-lg border border-border bg-background px-2 py-1.5 text-sm"
              >
                <option value="">Choose a suite…</option>
                {(suites.data ?? []).map((s) => (
                  <option key={s.suite_id} value={s.suite_id}>
                    {s.name} (v{s.dataset_version ?? 0}, {s.task_count} tasks)
                  </option>
                ))}
              </select>
            )}
          </label>
          <label className="block text-xs font-medium">
            Tags (comma-separated)
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="incident, ingest"
              className="mt-1 block w-full rounded-lg border border-border bg-background px-2 py-1.5 text-sm"
            />
          </label>
          <label className="block text-xs font-medium">
            Extra context for the task input (optional)
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              rows={2}
              maxLength={4000}
              className="mt-1 block w-full rounded-lg border border-border bg-background px-2 py-1.5 text-sm resize-none"
            />
          </label>
          <div className="flex flex-wrap gap-4 text-xs">
            <label className="inline-flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={includeTools}
                onChange={(e) => setIncludeTools(e.target.checked)}
              />
              Require the tools it called
            </label>
            <label className="inline-flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={includeCitations}
                onChange={(e) => setIncludeCitations(e.target.checked)}
              />
              Require the sources it cited
            </label>
          </div>
          {promote.isError && (
            <p role="alert" className="text-xs text-destructive">
              {(promote.error as Error)?.message || "The goal could not be promoted."}
            </p>
          )}
          <button
            type="submit"
            disabled={!suiteId || promote.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded-lg hover:opacity-90 disabled:opacity-50"
          >
            {promote.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
            Promote
          </button>
        </form>
      )}
    </div>
  );
}
