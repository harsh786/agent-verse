export interface ResultColumn {
  key: string;
  label: string;
  type: 'text' | 'link' | 'badge' | 'datetime' | 'number';
}

export interface ResultTable {
  title: string;
  columns: ResultColumn[];
  rows: Record<string, unknown>[];
}

export interface ResultArtifact {
  version: number;
  kind: 'table' | 'text' | 'cards' | 'json' | 'error' | 'empty';
  title: string;
  summary: string;
  status: 'success' | 'failed' | 'partial' | 'empty';
  metrics: Array<{ label: string; value: string | number }>;
  tables: ResultTable[];
  evidence: {
    tools?: Array<Record<string, unknown>>;
    verification?: string;
    query?: string;
    connector?: string;
  };
  downloads: Array<'json' | 'csv' | 'markdown'>;
  debug: Record<string, unknown>;
}

function csvEscape(value: unknown): string {
  const text = String(value ?? '');
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function markdownEscape(value: unknown): string {
  return String(value ?? '')
    .replace(/[\r\n]+/g, ' ')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/([\\`*_{}\[\]#+|])/g, '\\$1');
}

export function artifactToCsv(artifact: ResultArtifact): string {
  const table = artifact.tables[0];
  if (!table) return '';

  const header = table.columns.map((column) => csvEscape(column.label)).join(',');
  const rows = table.rows.map((row) =>
    table.columns.map((column) => csvEscape(row[column.key])).join(',')
  );

  return [header, ...rows].join('\n');
}

export function artifactToMarkdown(artifact: ResultArtifact): string {
  const lines = [`# ${markdownEscape(artifact.title)}`, '', markdownEscape(artifact.summary), ''];
  const table = artifact.tables[0];

  if (table) {
    lines.push(`## ${markdownEscape(table.title)}`, '');
    lines.push(`| ${table.columns.map((column) => markdownEscape(column.label)).join(' | ')} |`);
    lines.push(`| ${table.columns.map(() => '---').join(' | ')} |`);

    for (const row of table.rows) {
      lines.push(
        `| ${table.columns.map((column) => markdownEscape(row[column.key])).join(' | ')} |`
      );
    }
  }

  return lines.join('\n');
}
