import { Checkbox, Field, SecretInput, TlsFields, inputCls } from './fields';

interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }

/**
 * Redis Sources connector form (backend: app/ingestion/connectors/redis_connector.py).
 * Field names are the connector's connection_config keys. Secrets (password,
 * URL, Sentinel password, client key) are masked inputs; the API returns them as
 * "********" and keeps the stored value when that mask is sent back.
 */
export function RedisForm({ value, onChange }: FormProps) {
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  const mode = String(value.mode ?? 'standalone');
  const authType = String(value.auth_type ?? 'none');
  const str = (k: string) => String(value[k] ?? '');

  return (
    <div className="space-y-3">
      <Field label="Topology" htmlFor="redis-mode">
        <select id="redis-mode" value={mode} onChange={e => set('mode', e.target.value)} className={inputCls}>
          <option value="standalone">Standalone</option>
          <option value="sentinel">Sentinel (high availability)</option>
          <option value="cluster">Cluster</option>
        </select>
      </Field>

      {mode === 'standalone' && <>
        <SecretInput id="redis-uri" label="Redis URL (optional)" value={value.uri} onChange={v => set('uri', v)}
          placeholder="redis://user:password@host:6379/0 or rediss://…" hint="Or fill in host / port / database below. rediss:// turns on TLS." />
        <Field label="Host" htmlFor="redis-host"><input id="redis-host" type="text" value={str('host')} onChange={e => set('host', e.target.value)} placeholder="cache.example.com" className={inputCls} /></Field>
        <Field label="Port" htmlFor="redis-port"><input id="redis-port" type="number" value={String(value.port ?? 6379)} onChange={e => set('port', Number(e.target.value))} className={inputCls} /></Field>
        <Field label="Database" htmlFor="redis-db"><input id="redis-db" type="number" min="0" value={String(value.db ?? 0)} onChange={e => set('db', Number(e.target.value))} className={inputCls} /></Field>
      </>}

      {mode === 'sentinel' && <>
        <Field label="Sentinels" htmlFor="redis-sentinels" hint="Comma-separated host:port (default port 26379).">
          <input id="redis-sentinels" type="text" value={str('sentinels')} onChange={e => set('sentinels', e.target.value)} placeholder="sentinel-1:26379, sentinel-2:26379" className={inputCls} />
        </Field>
        <Field label="Master name" htmlFor="redis-master"><input id="redis-master" type="text" value={str('sentinel_master')} onChange={e => set('sentinel_master', e.target.value)} placeholder="mymaster" className={inputCls} /></Field>
        <Field label="Sentinel username (optional)" htmlFor="redis-sentinel-user"><input id="redis-sentinel-user" type="text" value={str('sentinel_username')} onChange={e => set('sentinel_username', e.target.value)} autoComplete="off" className={inputCls} /></Field>
        <SecretInput id="redis-sentinel-password" label="Sentinel password (optional)" value={value.sentinel_password} onChange={v => set('sentinel_password', v)}
          hint="The Sentinels' own requirepass — separate from the data password below." />
        <Field label="Database" htmlFor="redis-db"><input id="redis-db" type="number" min="0" value={String(value.db ?? 0)} onChange={e => set('db', Number(e.target.value))} className={inputCls} /></Field>
      </>}

      {mode === 'cluster' && (
        <Field label="Cluster nodes" htmlFor="redis-cluster-nodes" hint="Comma-separated seed host:port; the other nodes are discovered (and egress-checked).">
          <input id="redis-cluster-nodes" type="text" value={str('cluster_nodes')} onChange={e => set('cluster_nodes', e.target.value)} placeholder="node-1:7000, node-2:7001" className={inputCls} />
        </Field>
      )}

      <Field label="Authentication" htmlFor="redis-auth">
        <select id="redis-auth" value={authType} onChange={e => set('auth_type', e.target.value)} className={inputCls}>
          <option value="none">None</option>
          <option value="password">Password (requirepass)</option>
          <option value="acl">ACL username + password</option>
        </select>
      </Field>
      {authType === 'acl' && (
        <Field label="Username" htmlFor="redis-username"><input id="redis-username" type="text" value={str('username')} onChange={e => set('username', e.target.value)} autoComplete="off" className={inputCls} /></Field>
      )}
      {authType !== 'none' && (
        <SecretInput id="redis-password" label="Password" value={value.password} onChange={v => set('password', v)} />
      )}

      <TlsFields prefix="redis" value={value} set={set} />
      {Boolean(value.tls) && (
        <Checkbox id="redis-tls-hostname" label="Verify the certificate hostname" checked={value.tls_check_hostname !== false}
          onChange={v => set('tls_check_hostname', v)} hint="Turn off only when the server is reached by an IP its certificate does not name (e.g. a Sentinel-reported master)." />
      )}

      <Field label="Key patterns" htmlFor="redis-patterns" hint="Comma-separated globs; default * (everything).">
        <input id="redis-patterns" type="text" value={str('key_patterns')} onChange={e => set('key_patterns', e.target.value)} placeholder="docs:*, kb:*" className={inputCls} />
      </Field>
      <Field label="Types (optional)" htmlFor="redis-types" hint="Comma-separated subset of string, hash, list, set, zset, stream, json.">
        <input id="redis-types" type="text" value={str('types')} onChange={e => set('types', e.target.value)} placeholder="string, hash, json" className={inputCls} />
      </Field>
      <Field label="Max keys per sync" htmlFor="redis-max-keys"><input id="redis-max-keys" type="number" min="1" value={String(value.max_keys_per_sync ?? 10000)} onChange={e => set('max_keys_per_sync', Number(e.target.value))} className={inputCls} /></Field>
      <Field label="Max bytes per value" htmlFor="redis-max-bytes"><input id="redis-max-bytes" type="number" min="1" value={String(value.max_value_bytes ?? 1048576)} onChange={e => set('max_value_bytes', Number(e.target.value))} className={inputCls} /></Field>
      <Field label="Max items per hash / list / set / zset / stream" htmlFor="redis-max-items"><input id="redis-max-items" type="number" min="1" value={String(value.max_items ?? 1000)} onChange={e => set('max_items', Number(e.target.value))} className={inputCls} /></Field>
    </div>
  );
}
