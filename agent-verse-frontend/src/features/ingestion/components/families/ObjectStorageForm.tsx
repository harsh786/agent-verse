interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
const F = inputCls;
function set(value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, key: string, val: unknown) {
  onChange({ ...value, [key]: val });
}

/** S3-compatible types: the backend reads keys from ``connection_config.credentials``. */
const S3_LIKE = new Set(['s3', 'minio', 'r2']);

export function ObjectStorageForm({ sourceType, value, onChange }: FormProps) {
  const s = (k: string, v: unknown) => set(value, onChange, k, v);
  const s3Like = S3_LIKE.has(sourceType);
  const creds = (value.credentials && typeof value.credentials === 'object' ? value.credentials : {}) as Record<string, unknown>;
  /** Credentials of S3-like sources are nested (the connector never read the flat keys). */
  const cred = (k: string) => (s3Like ? creds[k] : value[k]);
  const setCred = (k: string, v: string) => {
    if (!s3Like) { s(k, v); return; }
    const next = { ...creds, [k]: v };
    if (!v) delete next[k];
    s('credentials', next);
  };
  return (
    <div className="space-y-3">
      {s3Like && <>
        <Field label="Bucket"><input type="text" value={String(value.bucket ?? '')} onChange={e => s('bucket', e.target.value)} placeholder="my-bucket" className={F} /></Field>
        <Field label="Key Prefix (optional)"><input type="text" value={String(value.prefix ?? '')} onChange={e => s('prefix', e.target.value)} placeholder="data/" className={F} /></Field>
        <Field label="Region"><input type="text" value={String(value.region ?? 'us-east-1')} onChange={e => s('region', e.target.value)} className={F} /></Field>
        <Field label={sourceType === 'minio' ? 'Endpoint URL' : 'Endpoint URL (optional, S3-compatible stores)'}>
          <input type="text" value={String(value.endpoint_url ?? '')} onChange={e => s('endpoint_url', e.target.value)}
            placeholder={sourceType === 'minio' ? 'http://minio:9000' : 'leave empty for AWS S3'} className={F} />
        </Field>
        <Field label="Addressing style">
          <select value={String(value.addressing_style ?? '')} onChange={e => s('addressing_style', e.target.value || undefined)} className={F}>
            <option value="">Default ({sourceType === 'minio' || value.endpoint_url ? 'path-style' : 'AWS'})</option>
            <option value="path">Path-style (host/bucket/key)</option>
            <option value="virtual">Virtual-hosted (bucket.host/key)</option>
            <option value="auto">Auto</option>
          </select>
        </Field>
      </>}
      <Field label="Access Key ID"><input type="text" value={String(cred('access_key_id') ?? '')} onChange={e => setCred('access_key_id', e.target.value)} className={F} /></Field>
      <Field label="Secret Access Key"><input type="password" value={String(cred('secret_access_key') ?? '')} onChange={e => setCred('secret_access_key', e.target.value)} autoComplete="new-password" className={F} /></Field>
      {s3Like && <Field label="Session Token (optional, temporary credentials)"><input type="password" value={String(cred('session_token') ?? '')} onChange={e => setCred('session_token', e.target.value)} autoComplete="new-password" className={F} /></Field>}
    </div>
  );
}
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="block text-sm font-medium mb-1">{label}</label>{children}</div>;
}
