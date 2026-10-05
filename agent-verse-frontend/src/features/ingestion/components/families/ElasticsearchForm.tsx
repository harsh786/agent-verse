import { Field, SecretInput, inputCls } from './fields';

interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }

/**
 * Elasticsearch / OpenSearch Sources connector form
 * (backend: app/ingestion/connectors/elasticsearch_connector.py). Field names are
 * the connector's connection_config keys; the password and API key are masked
 * ("********" comes back from the API and keeps the stored value).
 */
/** Switching the auth mode drops the other mode's credentials (never both sent). */
function withAuthMode(value: Record<string, unknown>, mode: string): Record<string, unknown> {
  const next: Record<string, unknown> = { ...value, auth_mode: mode };
  if (mode !== 'basic') { delete next.username; delete next.password; }
  if (mode !== 'api_key') delete next.api_key;
  return next;
}

export function ElasticsearchForm({ value, onChange }: FormProps) {
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  const str = (k: string) => String(value[k] ?? '');
  const authMode = String(value.auth_mode ?? (value.api_key ? 'api_key' : value.username ? 'basic' : 'none'));

  return (
    <div className="space-y-3">
      <Field label="Cluster URL" htmlFor="es-url" hint="http(s)://host:9200 — internal addresses are refused unless the operator allowlists them.">
        <input id="es-url" type="text" value={str('url')} onChange={e => set('url', e.target.value)} placeholder="https://search.example.com:9200" className={inputCls} />
      </Field>
      <Field label="Index" htmlFor="es-index" hint="An index, alias, data stream, comma list or pattern (logs-*). Every matching index is read.">
        <input id="es-index" type="text" value={str('index')} onChange={e => set('index', e.target.value)} placeholder="support-kb, logs-*" className={inputCls} />
      </Field>
      <Field label="Authentication" htmlFor="es-auth">
        <select id="es-auth" value={authMode} onChange={e => onChange(withAuthMode(value, e.target.value))} className={inputCls}>
          <option value="none">None</option>
          <option value="basic">Username + password</option>
          <option value="api_key">API key</option>
        </select>
      </Field>
      {authMode === 'basic' && <>
        <Field label="Username" htmlFor="es-username"><input id="es-username" type="text" value={str('username')} onChange={e => set('username', e.target.value)} autoComplete="off" className={inputCls} /></Field>
        <SecretInput id="es-password" label="Password" value={value.password} onChange={v => set('password', v)} />
      </>}
      {authMode === 'api_key' && (
        <SecretInput id="es-api-key" label="API key" value={value.api_key} onChange={v => set('api_key', v)}
          hint="The 'encoded' value Elasticsearch returns for a new API key (or id:key)." />
      )}
      <Field label="Sort field" htmlFor="es-sort" hint="A date (or numeric) field the index maps, ideally an update timestamp: each sync reads what changed since the last one. _doc reads everything every sync.">
        <input id="es-sort" type="text" value={str('sort_field')} onChange={e => set('sort_field', e.target.value)} placeholder="@timestamp" className={inputCls} />
      </Field>
      <Field label="Re-read window (seconds)" htmlFor="es-lookback" hint="Date sort fields: each sync also re-reads this window, so late-refreshed documents are not missed.">
        <input id="es-lookback" type="number" min="0" value={String(value.cursor_lookback_seconds ?? 60)} onChange={e => set('cursor_lookback_seconds', Number(e.target.value))} className={inputCls} />
      </Field>
      <Field label="Batch size" htmlFor="es-batch"><input id="es-batch" type="number" min="1" max="10000" value={String(value.batch_size ?? 500)} onChange={e => set('batch_size', Number(e.target.value))} className={inputCls} /></Field>
    </div>
  );
}
