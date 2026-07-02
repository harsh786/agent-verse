import { useId } from 'react';
import { AlertTriangle, CheckCircle, Database, FileJson } from 'lucide-react';
import type { AdaptiveColumnType, AdaptiveResultViewModel } from '../adaptiveResult';

type AdaptiveResultPanelProps = {
  result: AdaptiveResultViewModel;
  compact?: boolean;
};

function textValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '—';
  }

  return String(value);
}

function rawOutput(value: unknown): string {
  if (typeof value === 'string') {
    return JSON.stringify(value);
  }

  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

function validUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') {
    return undefined;
  }

  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? value : undefined;
  } catch {
    return undefined;
  }
}

function statusLabel(result: AdaptiveResultViewModel) {
  if (result.status === 'failed') {
    return {
      icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
      label: 'Failed',
      className: 'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-200',
    };
  }

  if (result.status === 'partial') {
    return {
      icon: <AlertTriangle className="h-4 w-4" aria-hidden="true" />,
      label: 'Partial',
      className: 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-200',
    };
  }

  if (result.status === 'empty') {
    return {
      icon: <Database className="h-4 w-4" aria-hidden="true" />,
      label: 'No rows',
      className: 'bg-slate-100 text-slate-700 dark:bg-slate-900/70 dark:text-slate-200',
    };
  }

  return {
    icon: <CheckCircle className="h-4 w-4" aria-hidden="true" />,
    label: 'Success',
    className: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200',
  };
}

function renderCell(value: unknown, type: AdaptiveColumnType, row: Record<string, unknown>) {
  const text = textValue(value);
  const contentClassName = 'break-words whitespace-pre-wrap';

  if (type === 'badge') {
    return (
      <span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-200">
        {text}
      </span>
    );
  }

  if (type === 'link' && value != null) {
    const href = validUrl(value) ?? validUrl(row.url);

    if (href) {
      return (
        <a className={`${contentClassName} text-primary underline-offset-4 hover:underline`} href={href}>
          {text}
        </a>
      );
    }
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

export function AdaptiveResultPanel({ result, compact = false }: AdaptiveResultPanelProps) {
  const headingId = useId();
  const tableTitleId = useId();
  const status = statusLabel(result);
  const panelPadding = compact ? 'p-4' : 'p-5';

  return (
    <section aria-labelledby={headingId} className={`rounded-2xl border bg-card ${panelPadding} shadow-sm`}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h3 className="break-words text-base font-semibold" id={headingId}>
            {result.title}
          </h3>
          {result.summary && <p className="mt-1 text-sm text-muted-foreground">{result.summary}</p>}
        </div>
        <span
          className={`inline-flex w-fit shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${status.className}`}
        >
          {status.icon}
          {status.label}
        </span>
      </div>

      {result.metrics.length > 0 && (
        <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {result.metrics.map((metric) => (
            <div key={metric.label} className="rounded-xl border bg-muted/20 px-3 py-2">
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {metric.label}
              </dt>
              <dd className="mt-1 break-words text-sm font-semibold text-foreground">
                {textValue(metric.value)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {result.primaryView === 'text' && result.text && (
        <div className="mt-4 break-words whitespace-pre-wrap rounded-xl border bg-muted/20 p-4 text-sm">
          {result.text}
        </div>
      )}

      {result.table && (
        <div className="mt-4 overflow-hidden rounded-xl border">
          <div className="flex items-center gap-2 border-b bg-muted/20 px-4 py-3">
            <Database className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <h4 className="text-sm font-semibold" id={tableTitleId}>
              {result.table.title}
            </h4>
          </div>
          <div className="overflow-x-auto">
            <table aria-labelledby={tableTitleId} className="w-full text-sm">
              <thead className="bg-muted/40">
                <tr>
                  {result.table.columns.map((column) => (
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
                {result.table.rows.map((row, rowIndex) => (
                  <tr key={rowIndex} className="border-t hover:bg-muted/30">
                    {result.table?.columns.map((column) => (
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
        </div>
      )}

      {result.diagnostics.length > 0 && (
        <dl className="mt-4 grid gap-3 text-sm md:grid-cols-2">
          {result.diagnostics.map((diagnostic) => (
            <div key={diagnostic.label} className="rounded-xl border bg-muted/20 px-3 py-2">
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {diagnostic.label}
              </dt>
              <dd className="mt-1 break-words text-foreground">{diagnostic.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {result.primaryView === 'json' && (
        <p className="mt-4 rounded-xl border bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
          Structured output is available in Raw output.
        </p>
      )}

      <details className="mt-4 rounded-xl border bg-muted/20 px-3 py-2 text-sm">
        <summary className="cursor-pointer font-semibold text-muted-foreground" role="button">
          <span className="inline-flex items-center gap-2">
            <FileJson className="h-4 w-4" aria-hidden="true" />
            Raw output
          </span>
        </summary>
        <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-background p-3 text-xs text-foreground">
          {rawOutput(result.raw)}
        </pre>
      </details>
    </section>
  );
}
