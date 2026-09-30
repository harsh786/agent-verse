interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="block text-sm font-medium mb-1">{label}</label>{children}</div>;
}
const set = (value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, key: string, val: unknown) => onChange({ ...value, [key]: val });
// The backend RSS connector (source types ``rss`` / ``atom``) reads ``url``.
const FEED_TYPES = new Set(['rss', 'atom', 'rss_feed']);

export function WebForm({ sourceType, value, onChange }: FormProps) {
  const s = (k: string, v: unknown) => set(value, onChange, k, v);
  if (FEED_TYPES.has(sourceType)) {
    return (
      <div className="space-y-3">
        <Field label="Feed URL"><input type="url" value={String(value.url ?? '')} onChange={e => s('url', e.target.value)} placeholder="https://example.com/feed.xml" className={inputCls} /></Field>
        <Field label="Max entries per sync"><input type="number" min="1" max="5000" value={String(value.max_entries ?? 200)} onChange={e => s('max_entries', Number(e.target.value))} className={inputCls} /></Field>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <Field label="Start URL(s)">
        <textarea rows={3} value={String(value.urls ?? '')} onChange={e => s('urls', e.target.value)} placeholder="https://docs.example.com&#10;https://example.com/blog" className={`${inputCls} resize-none`} />
      </Field>
      {sourceType === 'web_crawler' && <>
        <Field label="Max Depth"><input type="number" min="1" max="10" value={String(value.max_depth ?? 3)} onChange={e => s('max_depth', Number(e.target.value))} className={inputCls} /></Field>
        <Field label="URL Pattern (optional)"><input type="text" value={String(value.url_pattern ?? '')} onChange={e => s('url_pattern', e.target.value)} placeholder="https://docs.example.com/*" className={inputCls} /></Field>
      </>}
      {sourceType === 'sitemap' && <Field label="Sitemap URL"><input type="text" value={String(value.sitemap_url ?? '')} onChange={e => s('sitemap_url', e.target.value)} placeholder="https://example.com/sitemap.xml" className={inputCls} /></Field>}
    </div>
  );
}
