interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="block text-sm font-medium mb-1">{label}</label>{children}</div>;
}
const set = (value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, key: string, val: unknown) => onChange({ ...value, [key]: val });
export function CodeRepoForm({ sourceType, value, onChange }: FormProps) {
  const s = (k: string, v: unknown) => set(value, onChange, k, v);
  return (
    <div className="space-y-3">
      <Field label="Repository URL"><input type="text" value={String(value.repo_url ?? '')} onChange={e => s('repo_url', e.target.value)} placeholder="https://github.com/org/repo" className={inputCls} /></Field>
      <Field label="Branch"><input type="text" value={String(value.branch ?? 'main')} onChange={e => s('branch', e.target.value)} className={inputCls} /></Field>
      {(sourceType === 'github' || sourceType === 'gitlab') && (
        <Field label="Access Token"><input type="password" value={String(value.access_token ?? '')} onChange={e => s('access_token', e.target.value)} className={inputCls} autoComplete="new-password" /></Field>
      )}
      {sourceType === 'bitbucket' && <Field label="App Password"><input type="password" value={String(value.app_password ?? '')} onChange={e => s('app_password', e.target.value)} className={inputCls} autoComplete="new-password" /></Field>}
      <Field label="File Pattern (glob)"><input type="text" value={String(value.file_pattern ?? '**/*.{md,py,ts,js,txt}')} onChange={e => s('file_pattern', e.target.value)} className={inputCls} /></Field>
    </div>
  );
}
