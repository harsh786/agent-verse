/**
 * richOutput — parses an assistant message into an ordered list of rich
 * segments so the render path can promote tabular / chart / image output to
 * dedicated interactive components while leaving ordinary prose as markdown.
 *
 * Detection is deliberately conservative:
 *  - Explicit fenced blocks (```chart / ```image / ```table|data|datatable)
 *    are always promoted.
 *  - A text chunk is promoted only when the WHOLE chunk is exactly a markdown
 *    table, a JSON array of flat objects, or a single markdown image — so
 *    mixed prose (with an inline table or a normal ```js fence) still renders
 *    as one markdown block, preserving existing behaviour.
 */

export interface ChartPoint {
  label: string;
  value: number;
}

export type RichSegment =
  | { kind: 'markdown'; content: string }
  | { kind: 'table'; rows: Record<string, unknown>[]; title?: string }
  | { kind: 'chart'; data: ChartPoint[]; title?: string }
  | { kind: 'image'; src: string; alt?: string };

const SPECIAL_FENCE = /```(chart|image|table|datatable|data)\b[^\n]*\n([\s\S]*?)```/g;
const SINGLE_IMAGE = /^!\[([^\]]*)\]\(([^)\s]+)\)$/;

function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());
}

export function isMarkdownTable(text: string): boolean {
  const lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  if (lines.length < 2) return false;
  if (!lines.every((l) => l.includes('|'))) return false;
  const sep = lines[1];
  return /-/.test(sep) && /^\|?[\s:|-]+\|?$/.test(sep);
}

export function parseMarkdownTable(text: string): Record<string, unknown>[] {
  const lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);
  const headers = splitTableRow(lines[0]);
  const rows: Record<string, unknown>[] = [];
  for (const line of lines.slice(2)) {
    const cells = splitTableRow(line);
    const row: Record<string, unknown> = {};
    headers.forEach((h, i) => {
      row[h] = cells[i] ?? '';
    });
    rows.push(row);
  }
  return rows;
}

function asObjectArray(value: unknown): Record<string, unknown>[] | null {
  if (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((v) => v !== null && typeof v === 'object' && !Array.isArray(v))
  ) {
    return value as Record<string, unknown>[];
  }
  return null;
}

function tryJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function normalizeChart(value: unknown): { data: ChartPoint[]; title?: string } | null {
  const raw = Array.isArray(value)
    ? value
    : value && typeof value === 'object' && Array.isArray((value as { data?: unknown }).data)
      ? (value as { data: unknown[] }).data
      : null;
  if (!raw) return null;
  const data: ChartPoint[] = [];
  for (const p of raw) {
    if (p && typeof p === 'object') {
      const rec = p as Record<string, unknown>;
      const label = rec.label ?? rec.name ?? rec.x;
      const value = rec.value ?? rec.y ?? rec.count;
      if (label !== undefined && value !== undefined && !Number.isNaN(Number(value))) {
        data.push({ label: String(label), value: Number(value) });
      }
    }
  }
  if (data.length === 0) return null;
  const title =
    !Array.isArray(value) && value && typeof value === 'object'
      ? (value as { title?: unknown }).title
      : undefined;
  return { data, title: title != null ? String(title) : undefined };
}

function classifyText(text: string): RichSegment[] {
  const trimmed = text.trim();
  if (!trimmed) return [];

  if (isMarkdownTable(trimmed)) {
    return [{ kind: 'table', rows: parseMarkdownTable(trimmed) }];
  }

  const img = SINGLE_IMAGE.exec(trimmed);
  if (img) {
    return [{ kind: 'image', src: img[2], alt: img[1] || undefined }];
  }

  if (trimmed.startsWith('[')) {
    const rows = asObjectArray(tryJson(trimmed));
    if (rows) return [{ kind: 'table', rows }];
  }

  return [{ kind: 'markdown', content: text }];
}

function parseSpecialFence(lang: string, body: string): RichSegment {
  const trimmed = body.trim();
  if (lang === 'chart') {
    const chart = normalizeChart(tryJson(trimmed));
    if (chart) return { kind: 'chart', ...chart };
  } else if (lang === 'image') {
    const parsed = tryJson(trimmed);
    if (parsed && typeof parsed === 'object') {
      const rec = parsed as Record<string, unknown>;
      const src = rec.src ?? rec.url;
      if (typeof src === 'string') {
        return { kind: 'image', src, alt: rec.alt != null ? String(rec.alt) : undefined };
      }
    } else if (/^https?:\/\/|^data:/.test(trimmed)) {
      return { kind: 'image', src: trimmed };
    }
  } else {
    // table | datatable | data
    const parsed = tryJson(trimmed);
    const rows = asObjectArray(parsed);
    if (rows) return { kind: 'table', rows };
    if (parsed && typeof parsed === 'object') {
      const rec = parsed as Record<string, unknown>;
      const nested = asObjectArray(rec.rows ?? rec.data);
      if (nested) {
        return { kind: 'table', rows: nested, title: rec.title != null ? String(rec.title) : undefined };
      }
    }
    if (isMarkdownTable(trimmed)) return { kind: 'table', rows: parseMarkdownTable(trimmed) };
  }
  // Unparseable special block — preserve it verbatim as a fenced code block.
  return { kind: 'markdown', content: '```\n' + body + '\n```' };
}

export function parseRichSegments(content: string): RichSegment[] {
  if (!content) return [];
  const segments: RichSegment[] = [];
  let lastIndex = 0;
  SPECIAL_FENCE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = SPECIAL_FENCE.exec(content)) !== null) {
    const before = content.slice(lastIndex, match.index);
    segments.push(...classifyText(before));
    segments.push(parseSpecialFence(match[1], match[2]));
    lastIndex = SPECIAL_FENCE.lastIndex;
  }
  segments.push(...classifyText(content.slice(lastIndex)));

  return segments.length > 0 ? segments : [{ kind: 'markdown', content }];
}
