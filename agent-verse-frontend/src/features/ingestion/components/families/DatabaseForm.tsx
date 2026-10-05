import { useState } from 'react';
import { FieldError, SecretInput, TlsFields } from './fields';
interface FormProps {
  sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void;
  /** Server validation messages keyed by connection_config key (B1). */
  errors?: Record<string, string>;
}
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
function F({ label, error, children }: { label: string; error?: string; children: React.ReactNode }) {
  return <div><label className="block text-sm font-medium mb-1">{label}</label>{children}<FieldError error={error} /></div>;
}
function s(value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, k: string, v: unknown) { onChange({ ...value, [k]: v }); }

export function DatabaseForm({ sourceType, value, onChange, errors = {} }: FormProps) {
  const set = (k: string, v: unknown) => s(value, onChange, k, v);
  const isSnowflake = sourceType === 'snowflake';
  const isBigQuery  = sourceType === 'bigquery';
  const isMongo     = sourceType === 'mongodb';
  const isX509      = isMongo && String(value.auth_mechanism ?? '').toUpperCase() === 'MONGODB-X509';

  return (
    <div className="space-y-3">
      {!isSnowflake && !isBigQuery && !isMongo && <>
        <F label="Host" error={errors.host}><input type="text" value={String(value.host ?? '')} aria-invalid={errors.host ? true : undefined} onChange={e => set('host', e.target.value)} placeholder="db.example.com" className={inputCls} /></F>
        <F label="Port" error={errors.port}><input type="number" value={Number(value.port ?? (sourceType === 'postgresql' ? 5432 : 3306))} aria-invalid={errors.port ? true : undefined} onChange={e => set('port', Number(e.target.value))} className={inputCls} /></F>
        <F label="Database" error={errors.database}><input type="text" value={String(value.database ?? '')} aria-invalid={errors.database ? true : undefined} onChange={e => set('database', e.target.value)} className={inputCls} /></F>
        <F label="Username" error={errors.username}><input type="text" value={String(value.username ?? '')} aria-invalid={errors.username ? true : undefined} onChange={e => set('username', e.target.value)} className={inputCls} /></F>
        <F label="Password" error={errors.password}><input type="password" value={String(value.password ?? '')} aria-invalid={errors.password ? true : undefined} onChange={e => set('password', e.target.value)} autoComplete="new-password" className={inputCls} /></F>
        <F label="Tables (comma-separated)" error={errors.tables_csv}><input type="text" value={String(value.tables_csv ?? '')} aria-invalid={errors.tables_csv ? true : undefined} onChange={e => set('tables_csv', e.target.value)} placeholder="orders, customers" className={inputCls} /></F>
        {sourceType === 'postgresql' && <F label="CDC Mode" error={errors.cdc_mode}><select value={String(value.cdc_mode ?? 'query')} aria-invalid={errors.cdc_mode ? true : undefined} onChange={e => set('cdc_mode', e.target.value)} className={inputCls}><option value="query">Query (incremental)</option><option value="logical_replication">Logical Replication (CDC)</option><option value="pg_notify">pg_notify</option></select></F>}
      </>}
      {isSnowflake && <>
        <F label="Account" error={errors.account}><input type="text" value={String(value.account ?? '')} aria-invalid={errors.account ? true : undefined} onChange={e => set('account', e.target.value)} placeholder="org-account" className={inputCls} /></F>
        <F label="Warehouse" error={errors.warehouse}><input type="text" value={String(value.warehouse ?? '')} aria-invalid={errors.warehouse ? true : undefined} onChange={e => set('warehouse', e.target.value)} className={inputCls} /></F>
        <F label="Database" error={errors.database}><input type="text" value={String(value.database ?? '')} aria-invalid={errors.database ? true : undefined} onChange={e => set('database', e.target.value)} className={inputCls} /></F>
        <F label="Schema" error={errors.schema}><input type="text" value={String(value.schema ?? 'PUBLIC')} aria-invalid={errors.schema ? true : undefined} onChange={e => set('schema', e.target.value)} className={inputCls} /></F>
        <F label="Username" error={errors.username}><input type="text" value={String(value.username ?? '')} aria-invalid={errors.username ? true : undefined} onChange={e => set('username', e.target.value)} className={inputCls} /></F>
        <F label="Password" error={errors.password}><input type="password" value={String(value.password ?? '')} aria-invalid={errors.password ? true : undefined} onChange={e => set('password', e.target.value)} autoComplete="new-password" className={inputCls} /></F>
        <F label="Tables (comma-separated)" error={errors.tables_csv}><input type="text" value={String(value.tables_csv ?? '')} aria-invalid={errors.tables_csv ? true : undefined} onChange={e => set('tables_csv', e.target.value)} className={inputCls} /></F>
      </>}
      {isMongo && <>
        <SecretInput id="mongo-uri" label="Connection URI" value={value.uri} onChange={v => set('uri', v)} placeholder="mongodb+srv://..." error={errors.uri}
          hint="mongodb:// or mongodb+srv://. Credentials can go in the fields below instead (no URL-escaping needed)." />
        <F label="Database" error={errors.database}><input type="text" value={String(value.database ?? '')} aria-invalid={errors.database ? true : undefined} onChange={e => set('database', e.target.value)} className={inputCls} /></F>
        <F label="Collections (comma-separated)" error={errors.collections_csv}><input type="text" value={String(value.collections_csv ?? '')} aria-invalid={errors.collections_csv ? true : undefined} onChange={e => set('collections_csv', e.target.value)} placeholder="Leave blank for all collections" className={inputCls} /></F>
        <F label="Username" error={errors.username}><input type="text" value={String(value.username ?? '')} aria-invalid={errors.username ? true : undefined} onChange={e => set('username', e.target.value)} autoComplete="off" className={inputCls} /></F>
        <SecretInput id="mongo-password" label="Password" value={value.password} onChange={v => set('password', v)} error={errors.password} />
        <F label="Auth source" error={errors.auth_source}><input type="text" value={String(value.auth_source ?? '')} aria-invalid={errors.auth_source ? true : undefined} onChange={e => set('auth_source', e.target.value)} placeholder="admin" className={inputCls} /></F>
        <F label="Auth mechanism" error={errors.auth_mechanism}><select value={String(value.auth_mechanism ?? '')} aria-invalid={errors.auth_mechanism ? true : undefined} onChange={e => {
          // X.509 authenticates with a client certificate over TLS (B5): turn
          // TLS on so the certificate fields appear with the choice.
          const mechanism = e.target.value;
          onChange(mechanism === 'MONGODB-X509' ? { ...value, auth_mechanism: mechanism, tls: true } : { ...value, auth_mechanism: mechanism });
        }} className={inputCls}>
          <option value="">Default (SCRAM)</option><option value="SCRAM-SHA-256">SCRAM-SHA-256</option><option value="SCRAM-SHA-1">SCRAM-SHA-1</option>
          <option value="PLAIN">PLAIN (LDAP)</option><option value="MONGODB-X509">X.509 client certificate</option>
        </select></F>
        <F label="Incremental cursor field" error={errors.cursor_field}><input type="text" value={String(value.cursor_field ?? '')} aria-invalid={errors.cursor_field ? true : undefined} onChange={e => set('cursor_field', e.target.value)} placeholder="_id" className={inputCls} /></F>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(value.direct_connection)} onChange={e => set('direct_connection', e.target.checked)} />Direct connection (no replica-set discovery)</label>
        <TlsFields prefix="mongo" value={value} set={set}
          requiredReason={isX509 ? 'X.509 needs the client certificate and its private key below (TLS is required).' : undefined} />
        <MongoAdvanced value={value} onChange={onChange} errors={errors} />
      </>}
    </div>
  );
}

/** connection_config keys of the MongoDB connector's advanced options (mongodb_connector.py). */
const MONGO_ADVANCED_KEYS = ['host', 'port', 'replica_set', 'batch_size', 'max_documents_per_sync', 'timeout_ms'] as const;

/**
 * Advanced MongoDB options the backend reads but the form never offered (B4):
 * host/port (used when no URI is given), replica set, batch size, the per-sync
 * document cap and the connect / server-selection timeout. Collapsed unless
 * one is already set.
 */
function MongoAdvanced({ value, onChange, errors }: {
  value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; errors: Record<string, string>;
}) {
  const [open, setOpen] = useState(() => MONGO_ADVANCED_KEYS.some(k => value[k] !== undefined && value[k] !== ''));
  const setText = (k: string, v: string) => onChange({ ...value, [k]: v });
  /** Positive integers are sent as numbers; an emptied field is dropped (backend default). */
  const setNumber = (k: string, raw: string) => {
    const next = { ...value };
    if (raw.trim() === '') delete next[k];
    else next[k] = Number(raw);
    onChange(next);
  };
  const num = (k: string) => (value[k] === undefined || value[k] === null ? '' : String(value[k]));
  const text = (id: string, k: string, label: string, placeholder: string, hint?: string) => (
    <div>
      <label htmlFor={id} className="block text-sm font-medium mb-1">{label}</label>
      {hint && <p className="text-xs text-muted-foreground mb-1.5">{hint}</p>}
      <input id={id} type="text" value={String(value[k] ?? '')} onChange={e => setText(k, e.target.value)} placeholder={placeholder}
        aria-invalid={errors[k] ? true : undefined} className={inputCls} />
      <FieldError error={errors[k]} />
    </div>
  );
  const number = (id: string, k: string, label: string, placeholder: string, hint?: string) => (
    <div>
      <label htmlFor={id} className="block text-sm font-medium mb-1">{label}</label>
      {hint && <p className="text-xs text-muted-foreground mb-1.5">{hint}</p>}
      <input id={id} type="number" min={1} inputMode="numeric" value={num(k)} onChange={e => setNumber(k, e.target.value)} placeholder={placeholder}
        aria-invalid={errors[k] ? true : undefined} className={inputCls} />
      <FieldError error={errors[k]} />
    </div>
  );
  return (
    <div className="rounded-lg border border-border">
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open} aria-controls="mongo-advanced"
        className="flex w-full items-center justify-between px-3 py-2 text-sm font-medium hover:bg-muted/50">
        Advanced options <span aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div id="mongo-advanced" className="space-y-3 border-t border-border p-3">
          {text('mongo-host', 'host', 'Host', 'db1.example.com,db2.example.com',
            'Used only when no Connection URI is given. Comma-separate replica-set seeds (host or host:port).')}
          {number('mongo-port', 'port', 'Port', '27017', 'Default port for seeds without one.')}
          {text('mongo-replica-set', 'replica_set', 'Replica set', 'rs0')}
          {number('mongo-batch-size', 'batch_size', 'Batch size', '500', 'Documents fetched per round trip.')}
          {number('mongo-max-docs', 'max_documents_per_sync', 'Max documents per sync', '10000', 'Cap on documents read in one sync run.')}
          {number('mongo-timeout', 'timeout_ms', 'Timeout (ms)', '10000', 'Connect and server-selection timeout.')}
        </div>
      )}
    </div>
  );
}
