interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="block text-sm font-medium mb-1">{label}</label>{children}</div>;
}
const set = (value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, key: string, val: unknown) => onChange({ ...value, [key]: val });
export function GenericSourceForm({ sourceType: _sourceType, value, onChange }: FormProps) {
  const s = (k: string, v: unknown) => set(value, onChange, k, v);
  return (
    <div className="space-y-3">
      <Field label="Endpoint URL">
        <input type="text" value={String(value.endpoint_url ?? '')} onChange={e => s('endpoint_url', e.target.value)} placeholder="https://api.example.com/data" className={inputCls} />
      </Field>
      <Field label="Authentication Header (optional)">
        <input type="text" value={String(value.auth_header ?? '')} onChange={e => s('auth_header', e.target.value)} placeholder="Bearer <token>" className={inputCls} />
      </Field>
      <Field label="Extra Configuration (JSON)">
        <textarea rows={4} value={String(value.config_json ?? '{}')} onChange={e => s('config_json', e.target.value)} className={`${inputCls} resize-none font-mono text-xs`} />
      </Field>
    </div>
  );
}
