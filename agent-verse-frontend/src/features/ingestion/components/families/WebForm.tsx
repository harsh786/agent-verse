import { useState } from 'react';

interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
function Field({ label, htmlFor, hint, children }: { label: string; htmlFor?: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="block text-sm font-medium mb-1">{label}</label>
      {hint && <p className="text-xs text-muted-foreground mb-1.5">{hint}</p>}
      {children}
    </div>
  );
}
const set = (value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, key: string, val: unknown) => onChange({ ...value, [key]: val });
// The backend RSS connector (source types ``rss`` / ``atom``) reads ``url``.
const FEED_TYPES = new Set(['rss', 'atom', 'rss_feed']);

/** Textarea text -> URL list (one per line or comma-separated). */
function toUrlList(text: string): string[] {
  return text.split(/[\n,]/).map(u => u.trim()).filter(Boolean);
}
function fromUrlList(v: unknown): string {
  if (Array.isArray(v)) return v.map(String).join('\n');
  return String(v ?? '');
}

/** Seed URL textarea: keeps the raw text (so Enter starts a new line) and emits a list. */
function SeedUrls({ value, onChange }: { value: unknown; onChange: (v: string[]) => void }) {
  const [text, setText] = useState(() => fromUrlList(value));
  return (
    <textarea id="crawl-seeds" rows={3} value={text} onChange={e => { setText(e.target.value); onChange(toUrlList(e.target.value)); }}
      placeholder="https://docs.example.com&#10;https://example.com/blog" className={`${inputCls} resize-none`} />
  );
}

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
  if (sourceType === 'web_crawl') {
    // Keys match app/ingestion/connectors/web_crawl_connector.py.
    return (
      <div className="space-y-3">
        <Field label="Seed URLs" htmlFor="crawl-seeds" hint="One per line. Same-site links found on these pages are followed up to the max depth.">
          <SeedUrls value={value.seed_urls} onChange={v => s('seed_urls', v)} />
        </Field>
        <Field label="Sitemap URL (optional)" htmlFor="crawl-sitemap" hint="Every <loc> in the sitemap is added to the crawl.">
          <input id="crawl-sitemap" type="url" value={String(value.sitemap_url ?? '')} onChange={e => s('sitemap_url', e.target.value)} placeholder="https://example.com/sitemap.xml" className={inputCls} />
        </Field>
        <Field label="Max Depth" htmlFor="crawl-depth"><input id="crawl-depth" type="number" min="1" max="10" value={String(value.max_depth ?? 3)} onChange={e => s('max_depth', Number(e.target.value))} className={inputCls} /></Field>
        <Field label="Max pages per sync" htmlFor="crawl-pages"><input id="crawl-pages" type="number" min="1" max="10000" value={String(value.max_pages ?? 100)} onChange={e => s('max_pages', Number(e.target.value))} className={inputCls} /></Field>
        <Field label="Include URL pattern (regex, optional)" htmlFor="crawl-include"><input id="crawl-include" type="text" value={String(value.include_url_pattern ?? '')} onChange={e => s('include_url_pattern', e.target.value)} placeholder="^https://docs\.example\.com/" className={inputCls} /></Field>
        <Field label="Exclude URL pattern (regex, optional)" htmlFor="crawl-exclude"><input id="crawl-exclude" type="text" value={String(value.exclude_url_pattern ?? '')} onChange={e => s('exclude_url_pattern', e.target.value)} placeholder="/admin/" className={inputCls} /></Field>
        <Field label="Delay between requests (seconds)" htmlFor="crawl-delay"><input id="crawl-delay" type="number" min="0" step="0.1" value={String(value.crawl_delay_seconds ?? 1)} onChange={e => s('crawl_delay_seconds', Number(e.target.value))} className={inputCls} /></Field>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <Field label="Start URL(s)">
        <textarea rows={3} value={String(value.urls ?? '')} onChange={e => s('urls', e.target.value)} placeholder="https://docs.example.com&#10;https://example.com/blog" className={`${inputCls} resize-none`} />
      </Field>
    </div>
  );
}
