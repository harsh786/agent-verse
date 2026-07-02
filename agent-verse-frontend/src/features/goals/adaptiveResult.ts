export type AdaptiveColumnType = 'text' | 'link' | 'badge' | 'datetime' | 'number';
export type AdaptiveResultStatus = 'success' | 'failed' | 'empty' | 'partial';
export type AdaptivePrimaryView = 'table' | 'text' | 'json' | 'diagnostic';

export interface AdaptiveResultColumn {
  key: string;
  label: string;
  type: AdaptiveColumnType;
}

export interface AdaptiveResultTable {
  title: string;
  columns: AdaptiveResultColumn[];
  rows: Record<string, unknown>[];
}

export interface AdaptiveResultViewModel {
  status: AdaptiveResultStatus;
  title: string;
  summary?: string;
  metrics: Array<{ label: string; value: string | number | boolean }>;
  primaryView: AdaptivePrimaryView;
  table?: AdaptiveResultTable;
  text?: string;
  diagnostics: Array<{ label: string; value: string }>;
  raw: unknown;
}

export interface AdaptiveResultContext {
  toolName?: string;
  serverId?: string;
  success?: boolean;
  error?: unknown;
}

const ARRAY_KEYS = [
  'issues',
  'items',
  'results',
  'values',
  'nodes',
  'pull_requests',
  'tickets',
  'tasks',
  'users',
  'files',
  'messages',
] as const;

const COLUMN_PRIORITY = [
  'key',
  'id',
  'number',
  'identifier',
  'name',
  'title',
  'summary',
  'text',
  'status',
  'state',
  'priority',
  'type',
  'issue_type',
  'assignee',
  'owner',
  'author',
  'reporter',
  'created_by',
  'created',
  'created_at',
  'updated',
  'updated_at',
  'last_seen',
  'ts',
] as const;

const HIDDEN_COLUMN_KEYS = new Set(['url', 'html_url', 'web_url', 'self']);
const IDENTITY_LINK_KEYS = new Set(['number', 'key', 'identifier', 'id', 'name', 'title']);

const METRIC_LABELS: Record<string, string> = {
  total: 'Total',
  total_count: 'Total',
  returned: 'Returned',
  max_results: 'Max Results',
  maxResults: 'Max Results',
  is_complete: 'Complete',
  isLast: 'Last',
};

type ResultArrayMatch = {
  key: string;
  rows: unknown[];
};

export function normalizeAdaptiveResult(
  output: unknown,
  context: AdaptiveResultContext = {}
): AdaptiveResultViewModel {
  const raw = output;
  const normalizedOutput = parseStructuredString(output) ?? output;

  if (context.success === false || isMeaningfulError(context.error) || hasOutputError(normalizedOutput)) {
    return {
      status: 'failed',
      title: titleFromContext(context, 'Tool failed'),
      metrics: extractMetrics(normalizedOutput),
      primaryView: 'diagnostic',
      diagnostics: buildFailureDiagnostics(normalizedOutput, context),
      raw,
    };
  }

  if (typeof normalizedOutput === 'string') {
    return {
      status: normalizedOutput.length === 0 ? 'empty' : 'success',
      title: titleFromContext(context, 'Result'),
      metrics: [],
      primaryView: 'text',
      text: normalizedOutput,
      diagnostics: [],
      raw,
    };
  }

  const resultArray = findResultArray(normalizedOutput);
  const metrics = extractMetrics(normalizedOutput);

  if (resultArray) {
    if (resultArray.rows.length === 0) {
      return {
        status: 'empty',
        title: titleFromContext(context, humanizeKey(resultArray.key)),
        metrics,
        primaryView: 'diagnostic',
        diagnostics: buildEmptyDiagnostics(normalizedOutput, context),
        raw,
      };
    }

    const rows = resultArray.rows.map(normalizeRow);

    if (!metrics.some((metric) => metric.label === 'Returned')) {
      metrics.push({ label: 'Returned', value: rows.length });
    }

    return {
      status: isPartial(normalizedOutput) ? 'partial' : 'success',
      title: titleFromContext(context, humanizeKey(resultArray.key)),
      metrics,
      primaryView: 'table',
      table: {
        title: humanizeKey(resultArray.key),
        columns: inferColumns(rows),
        rows,
      },
      diagnostics: [],
      raw,
    };
  }

  if (normalizedOutput === null || normalizedOutput === undefined) {
    return {
      status: 'empty',
      title: titleFromContext(context, 'Result'),
      metrics,
      primaryView: 'diagnostic',
      diagnostics: buildEmptyDiagnostics(normalizedOutput, context),
      raw,
    };
  }

  return {
    status: 'success',
    title: titleFromContext(context, 'Result'),
    metrics,
    primaryView: 'json',
    diagnostics: [],
    raw,
  };
}

function parseStructuredString(value: unknown): unknown | undefined {
  if (typeof value !== 'string') {
    return undefined;
  }

  const trimmed = value.trim();
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) {
    return undefined;
  }

  try {
    const parsed: unknown = JSON.parse(trimmed);
    return isRecord(parsed) || Array.isArray(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

function findResultArray(value: unknown): ResultArrayMatch | undefined {
  if (Array.isArray(value)) {
    return { key: 'results', rows: value };
  }

  if (!isRecord(value)) {
    return undefined;
  }

  for (const key of ARRAY_KEYS) {
    const child = value[key];
    if (Array.isArray(child)) {
      return { key, rows: child };
    }
  }

  for (const child of Object.values(value)) {
    const match = findResultArray(child);
    if (match) {
      return match;
    }
  }

  return undefined;
}

function extractMetrics(value: unknown): Array<{ label: string; value: string | number | boolean }> {
  const metrics: Array<{ label: string; value: string | number | boolean }> = [];
  const seen = new Set<string>();

  collectMetrics(value, metrics, seen);

  return metrics;
}

function collectMetrics(
  value: unknown,
  metrics: Array<{ label: string; value: string | number | boolean }>,
  seen: Set<string>
): void {
  if (Array.isArray(value)) {
    return;
  }

  if (!isRecord(value)) {
    return;
  }

  for (const [key, child] of Object.entries(value)) {
    const label = METRIC_LABELS[key];
    if (label && !seen.has(label) && isMetricValue(child)) {
      metrics.push({ label, value: child });
      seen.add(label);
    }
  }

  for (const [key, child] of Object.entries(value)) {
    if (!ARRAY_KEYS.includes(key as (typeof ARRAY_KEYS)[number])) {
      collectMetrics(child, metrics, seen);
    }
  }
}

function normalizeRow(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) {
    return { value };
  }

  const row: Record<string, unknown> = {};

  for (const [key, child] of Object.entries(value)) {
    if (key === 'html_url' || key === 'web_url') {
      row.url = child;
      continue;
    }

    if (key === 'user') {
      const display = displayValue(child);
      row.owner = display ?? child;
      continue;
    }

    if (key === 'state' && isRecord(child)) {
      const display = displayValue(child);
      row.status = display ?? child;
      continue;
    }

    if (['assignee', 'author', 'owner', 'reporter', 'created_by'].includes(key)) {
      row[key] = displayValue(child) ?? child;
      continue;
    }

    row[key] = isRecord(child) ? displayValue(child) ?? child : child;
  }

  return row;
}

function inferColumns(rows: Record<string, unknown>[]): AdaptiveResultColumn[] {
  const rowKeys = new Set(rows.flatMap((row) => Object.keys(row)));
  const orderedKeys = [
    ...COLUMN_PRIORITY.filter((key) => rowKeys.has(key)),
    ...Array.from(rowKeys).filter((key) => !COLUMN_PRIORITY.includes(key as never)),
  ].filter((key) => !HIDDEN_COLUMN_KEYS.has(key));

  return orderedKeys.slice(0, 7).map((key) => ({
    key,
    label: humanizeKey(key),
    type: inferColumnType(key, rows),
  }));
}

function inferColumnType(key: string, rows: Record<string, unknown>[]): AdaptiveColumnType {
  if (key === 'url') {
    return 'link';
  }

  if (IDENTITY_LINK_KEYS.has(key) && rows.some((row) => typeof row.url === 'string')) {
    return 'link';
  }

  if (['status', 'state', 'priority', 'type', 'issue_type'].includes(key)) {
    return 'badge';
  }

  if (['created', 'created_at', 'updated', 'updated_at', 'last_seen', 'ts'].includes(key)) {
    return 'datetime';
  }

  if (rows.some((row) => typeof row[key] === 'number')) {
    return 'number';
  }

  if (rows.some((row) => typeof row[key] === 'string' && /^https?:\/\//.test(row[key]))) {
    return 'link';
  }

  return 'text';
}

function buildFailureDiagnostics(
  output: unknown,
  context: AdaptiveResultContext
): Array<{ label: string; value: string }> {
  const diagnostics = buildContextDiagnostics(context);
  const error = context.error ?? (isRecord(output) ? output.error : undefined);

  if (isMeaningfulError(error)) {
    diagnostics.push({ label: 'Error', value: stringifyDiagnosticValue(error) });
  }

  return diagnostics;
}

function buildEmptyDiagnostics(
  output: unknown,
  context: AdaptiveResultContext
): Array<{ label: string; value: string }> {
  const diagnostics = buildContextDiagnostics(context);
  const query = findFirstString(output, ['jql', 'query', 'q']);

  if (query) {
    diagnostics.push({ label: 'Query', value: query });
  }

  diagnostics.push({ label: 'Result', value: 'No rows returned.' });

  return diagnostics;
}

function buildContextDiagnostics(context: AdaptiveResultContext): Array<{ label: string; value: string }> {
  const diagnostics: Array<{ label: string; value: string }> = [];

  if (context.toolName) {
    diagnostics.push({ label: 'Tool', value: context.toolName });
  }

  if (context.serverId) {
    diagnostics.push({ label: 'Server', value: context.serverId });
  }

  return diagnostics;
}

function findFirstString(value: unknown, keys: string[]): string | undefined {
  if (Array.isArray(value) || !isRecord(value)) {
    return undefined;
  }

  for (const key of keys) {
    const child = value[key];
    if (typeof child === 'string') {
      return child;
    }
  }

  for (const child of Object.values(value)) {
    const match = findFirstString(child, keys);
    if (match) {
      return match;
    }
  }

  return undefined;
}

function displayValue(value: unknown): unknown {
  if (!isRecord(value)) {
    return value;
  }

  for (const key of ['name', 'login', 'displayName', 'email', 'username', 'key', 'id', 'title']) {
    const child = value[key];
    if (typeof child === 'string' || typeof child === 'number') {
      return child;
    }
  }

  return undefined;
}

function hasOutputError(output: unknown): boolean {
  return isRecord(output) && isMeaningfulError(output.error);
}

function isMeaningfulError(value: unknown): boolean {
  if (value === null || value === undefined || value === false) {
    return false;
  }

  if (typeof value === 'string') {
    return value.trim().length > 0;
  }

  if (value instanceof Error) {
    return value.message.trim().length > 0;
  }

  if (isRecord(value)) {
    return Object.keys(value).length > 0;
  }

  return true;
}

function isPartial(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }

  if (value.is_complete === false || value.isLast === false) {
    return true;
  }

  return Object.values(value).some((child) => !Array.isArray(child) && isPartial(child));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isMetricValue(value: unknown): value is string | number | boolean {
  return ['string', 'number', 'boolean'].includes(typeof value);
}

function titleFromContext(context: AdaptiveResultContext, fallback: string): string {
  if (context.toolName) {
    return humanizeKey(context.toolName);
  }

  if (context.serverId) {
    return humanizeKey(context.serverId);
  }

  return fallback;
}

function humanizeKey(key: string): string {
  const words = key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim();

  if (words.length === 0) {
    return 'Result';
  }

  return words.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function stringifyDiagnosticValue(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }

  if (value instanceof Error) {
    return value.message;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
