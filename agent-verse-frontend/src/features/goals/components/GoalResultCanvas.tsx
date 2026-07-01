import { useId } from 'react';
import type { ResultArtifact, ResultColumn } from '../resultArtifact';

function toolName(tool: Record<string, unknown>) {
  const name = tool.name ?? tool.tool ?? tool.tool_name;
  return typeof name === 'string' && name.length > 0 ? name : undefined;
}

function lastSuccessfulTool(artifact: ResultArtifact) {
  return [...(artifact.evidence.tools ?? [])].reverse().find((tool) => tool.success === true);
}

function renderCell(value: unknown, type: ResultColumn['type'], row: Record<string, unknown>) {
  const text = String(value ?? '—');
  const contentClassName = 'break-words whitespace-pre-wrap';

  if (type === 'badge') {
    return (
      <span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
        {text}
      </span>
    );
  }

  if (type === 'link' && typeof row.url === 'string' && row.url.length > 0 && value != null) {
    return (
      <a className={`${contentClassName} text-primary underline-offset-4 hover:underline`} href={row.url}>
        {text}
      </a>
    );
  }

  if (type === 'datetime' && value != null) {
    return (
      <time className={contentClassName} dateTime={text}>
        {text}
      </time>
    );
  }

  return <span className={contentClassName}>{text}</span>;
}

type GoalResultCanvasProps = {
  artifact: ResultArtifact;
  onShowExecution?: () => void;
};

export function GoalResultCanvas({ artifact, onShowExecution }: GoalResultCanvasProps) {
  const tableTitleId = useId();

  if (artifact.status === 'failed') {
    const successfulTool = lastSuccessfulTool(artifact);
    const successfulToolName = successfulTool ? toolName(successfulTool) : undefined;
    const suggestedAction = artifact.evidence.verification || 'Review the failed step, fix the connector or input, and rerun the goal.';

    return (
      <section className="rounded-2xl border border-red-200 bg-red-50 p-5 text-red-950 shadow-sm dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-100">
        <div className="rounded-full bg-red-100 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-red-700 dark:bg-red-900/50 dark:text-red-200">
          Result failed
        </div>
        <h3 className="mt-4 text-lg font-semibold">{artifact.title}</h3>
        <p className="mt-2 text-sm text-red-800 dark:text-red-200">{artifact.summary}</p>
        <dl className="mt-5 grid gap-3 sm:grid-cols-2">
          {successfulToolName && (
            <div className="rounded-xl border border-red-200 bg-white/70 p-3 dark:border-red-900/60 dark:bg-red-950/40">
              <dt className="text-xs font-semibold uppercase tracking-wide text-red-700 dark:text-red-300">
                Last successful tool
              </dt>
              <dd className="mt-1 break-words text-sm font-medium">{successfulToolName}</dd>
            </div>
          )}
          <div className="rounded-xl border border-red-200 bg-white/70 p-3 dark:border-red-900/60 dark:bg-red-950/40">
            <dt className="text-xs font-semibold uppercase tracking-wide text-red-700 dark:text-red-300">
              Suggested next action
            </dt>
            <dd className="mt-1 break-words text-sm font-medium">{suggestedAction}</dd>
          </div>
        </dl>
        {onShowExecution ? (
          <button
            className="mt-4 inline-flex rounded-lg bg-red-700 px-3 py-2 text-sm font-semibold text-white shadow-sm hover:bg-red-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-700 dark:bg-red-600 dark:hover:bg-red-500"
            type="button"
            onClick={onShowExecution}
          >
            Open Execution tab
          </button>
        ) : (
          <p className="mt-4 text-sm text-red-800 dark:text-red-200">
            <span className="font-semibold">Use the Execution tab</span> to inspect the full tool timeline and raw failure details.
          </p>
        )}
      </section>
    );
  }

  if (artifact.status === 'empty' || artifact.kind === 'empty') {
    return (
      <div className="rounded-2xl border border-dashed bg-muted/20 p-8 text-center">
        <h3 className="text-lg font-semibold">{artifact.title || 'No matching results'}</h3>
        <p className="mt-2 text-sm text-muted-foreground">
          {artifact.summary || 'The agent completed successfully, but the source returned no rows.'}
        </p>
      </div>
    );
  }

  const table = artifact.tables[0];
  if (!table) {
    return (
      <div className="space-y-3">
        {artifact.status === 'partial' && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100">
            This result may be incomplete. Review the Execution tab for the failed or skipped step.
          </div>
        )}
        <div className="break-words whitespace-pre-wrap rounded-2xl border bg-card p-5">{artifact.summary}</div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {artifact.status === 'partial' && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100">
          This result may be incomplete. Review the Execution tab for the failed or skipped step.
        </div>
      )}
      <section className="rounded-2xl border bg-card shadow-sm">
        <div className="border-b px-5 py-4">
          <h3 className="text-base font-semibold" id={tableTitleId}>
            {table.title}
          </h3>
          <p className="text-sm text-muted-foreground">{artifact.summary}</p>
        </div>
        <div className="overflow-x-auto">
          <table aria-labelledby={tableTitleId} className="w-full text-sm">
            <thead className="bg-muted/40">
              <tr>
                {table.columns.map((column) => (
                  <th
                    key={column.key}
                    className={`px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground ${column.type === 'number' ? 'text-right' : 'text-left'}`}
                    scope="col"
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-t hover:bg-muted/30">
                  {table.columns.map((column) => (
                    <td
                      key={column.key}
                      className={`px-4 py-3 align-top ${column.type === 'number' ? 'text-right' : ''}`}
                    >
                      {renderCell(row[column.key], column.type, row)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
