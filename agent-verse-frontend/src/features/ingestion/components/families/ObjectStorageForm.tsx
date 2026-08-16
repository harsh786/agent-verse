interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
const F = inputCls;
function set(value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, key: string, val: unknown) {
  onChange({ ...value, [key]: val });
}
export function ObjectStorageForm({ sourceType, value, onChange }: FormProps) {
  const s = (k: string, v: unknown) => set(value, onChange, k, v);
  return (
    <div className="space-y-3">
      {(sourceType === 's3' || sourceType === 'minio' || sourceType === 'r2') && <>
        <Field label="Bucket"><input type="text" value={String(value.bucket ?? '')} onChange={e => s('bucket', e.target.value)} placeholder="my-bucket" className={F} /></Field>
        <Field label="Key Prefix (optional)"><input type="text" value={String(value.prefix ?? '')} onChange={e => s('prefix', e.target.value)} placeholder="data/" className={F} /></Field>
        <Field label="Region"><input type="text" value={String(value.region ?? 'us-east-1')} onChange={e => s('region', e.target.value)} className={F} /></Field>
      </>}
      {sourceType === 'minio' && <Field label="Endpoint URL"><input type="text" value={String(value.endpoint_url ?? '')} onChange={e => s('endpoint_url', e.target.value)} placeholder="http://minio:9000" className={F} /></Field>}
      <Field label="Access Key ID"><input type="text" value={String(value.access_key_id ?? '')} onChange={e => s('access_key_id', e.target.value)} className={F} /></Field>
      <Field label="Secret Access Key"><input type="password" value={String(value.secret_access_key ?? '')} onChange={e => s('secret_access_key', e.target.value)} autoComplete="new-password" className={F} /></Field>
    </div>
  );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="block text-sm font-medium mb-1">{label}</label>{children}</div>;
}
