/**
 * KeyValueEditor — add/remove-row editor for a dict-shaped field
 * (HTTP headers, tool arguments, event payloads, RPA selectors, …).
 *
 * Serializes back to the exact `Record<string, unknown>` shape the node stores,
 * so the generated workflow definition / YAML stays valid. Values are parsed
 * leniently: valid JSON literals (numbers, booleans, null, objects, arrays,
 * quoted strings) round-trip as their real type; anything else is kept as a
 * plain string — which preserves template refs like `{{inputs.x}}`.
 *
 * A "raw JSON" toggle exposes the underlying object for power users; edits there
 * feed back into the same onChange contract.
 *
 * NOTE: this component keeps local draft state for in-progress key edits (a dict
 * cannot represent an empty or duplicate key mid-typing). Callers should pass a
 * `key={nodeId}` so switching the selected node re-seeds the editor.
 */
import { useState, type ReactNode } from 'react';
import { Plus, Trash2, Braces, Rows3 } from 'lucide-react';
import { INPUT_CLS, FieldShell } from './FormFields';

type Dict = Record<string, unknown>;
interface Row { k: string; v: string }

function valueToInput(v: unknown): string {
  if (typeof v === 'string') return v;
  if (v === null || v === undefined) return '';
  try { return JSON.stringify(v); } catch { return String(v); }
}

function inputToValue(s: string): unknown {
  const t = s.trim();
  if (t === '') return '';
  // Only re-type things that look like JSON literals; leave templates/plain
  // strings (e.g. "{{inputs.x}}", "hello") untouched.
  if (/^(true|false|null|-?\d+(\.\d+)?([eE][+-]?\d+)?|\[.*\]|\{.*\}|".*")$/.test(t)) {
    try { return JSON.parse(t); } catch { /* fall through */ }
  }
  return s;
}

function dictToRows(value: Dict | undefined): Row[] {
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).map(([k, v]) => ({ k, v: valueToInput(v) }));
}

function rowsToDict(rows: Row[]): Dict {
  const out: Dict = {};
  for (const { k, v } of rows) {
    if (k.trim() === '') continue;
    out[k] = inputToValue(v);
  }
  return out;
}

export function KeyValueEditor({
  label, value, onChange, description,
  keyPlaceholder = 'key', valuePlaceholder = 'value',
}: {
  label: string;
  value: Dict | undefined;
  onChange: (v: Dict) => void;
  description?: ReactNode;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const [rows, setRows] = useState<Row[]>(() => dictToRows(value));
  const [raw, setRaw] = useState(false);
  const [rawText, setRawText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [rawError, setRawError] = useState<string | null>(null);

  const commit = (next: Row[]) => {
    setRows(next);
    onChange(rowsToDict(next));
  };

  const enterRaw = () => {
    setRawText(JSON.stringify(rowsToDict(rows), null, 2));
    setRawError(null);
    setRaw(true);
  };

  const exitRaw = () => {
    try {
      const parsed = rawText.trim() === '' ? {} : JSON.parse(rawText);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setRawError('Must be a JSON object ({ … }).');
        return;
      }
      const dict = parsed as Dict;
      setRows(dictToRows(dict));
      onChange(dict);
      setRawError(null);
      setRaw(false);
    } catch {
      setRawError('Invalid JSON.');
    }
  };

  const toggle = (
    <button
      type="button"
      onClick={raw ? exitRaw : enterRaw}
      className="flex items-center gap-1 text-[10px] font-medium text-white/40 hover:text-sky-300
                 transition-colors"
    >
      {raw ? <Rows3 className="h-3 w-3" /> : <Braces className="h-3 w-3" />}
      {raw ? 'Rows' : 'Raw JSON'}
    </button>
  );

  return (
    <FieldShell label={label} description={description} action={toggle}>
      {raw ? (
        <>
          <textarea
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            onBlur={exitRaw}
            rows={6}
            spellCheck={false}
            placeholder="{ }"
            className={`${INPUT_CLS} resize-y font-mono`}
          />
          {rawError && <p className="text-xs text-red-400 mt-1">{rawError}</p>}
        </>
      ) : (
        <div className="space-y-1.5">
          {rows.length === 0 && (
            <p className="text-xs text-white/25 italic">No entries yet.</p>
          )}
          {rows.map((row, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <input
                type="text"
                value={row.k}
                onChange={(e) => commit(rows.map((r, j) => (j === i ? { ...r, k: e.target.value } : r)))}
                placeholder={keyPlaceholder}
                className={`${INPUT_CLS} flex-[2] font-mono`}
                aria-label={`${label} key ${i + 1}`}
              />
              <input
                type="text"
                value={row.v}
                onChange={(e) => commit(rows.map((r, j) => (j === i ? { ...r, v: e.target.value } : r)))}
                placeholder={valuePlaceholder}
                className={`${INPUT_CLS} flex-[3] font-mono`}
                aria-label={`${label} value ${i + 1}`}
              />
              <button
                type="button"
                onClick={() => commit(rows.filter((_, j) => j !== i))}
                className="shrink-0 p-1.5 rounded-lg text-white/30 hover:text-red-400
                           hover:bg-red-500/10 transition-colors"
                aria-label={`Remove ${label} row ${i + 1}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => setRows([...rows, { k: '', v: '' }])}
            className="flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs text-white/50
                       border border-dashed border-white/15 hover:border-sky-500/50
                       hover:text-sky-300 transition-colors w-full justify-center"
          >
            <Plus className="h-3.5 w-3.5" />
            Add row
          </button>
        </div>
      )}
    </FieldShell>
  );
}
