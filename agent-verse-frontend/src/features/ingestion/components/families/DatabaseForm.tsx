interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
function F({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="block text-sm font-medium mb-1">{label}</label>{children}</div>;
}
function s(value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, k: string, v: unknown) { onChange({ ...value, [k]: v }); }

export function DatabaseForm({ sourceType, value, onChange }: FormProps) {
  const set = (k: string, v: unknown) => s(value, onChange, k, v);
  const isSnowflake = sourceType === 'snowflake';
  const isBigQuery  = sourceType === 'bigquery';
  const isMongo     = sourceType === 'mongodb';

  return (
    <div className="space-y-3">
      {!isSnowflake && !isBigQuery && !isMongo && <>
        <F label="Host"><input type="text" value={String(value.host ?? '')} onChange={e => set('host', e.target.value)} placeholder="db.example.com" className={inputCls} /></F>
        <F label="Port"><input type="number" value={Number(value.port ?? (sourceType === 'postgresql' ? 5432 : 3306))} onChange={e => set('port', Number(e.target.value))} className={inputCls} /></F>
        <F label="Database"><input type="text" value={String(value.database ?? '')} onChange={e => set('database', e.target.value)} className={inputCls} /></F>
        <F label="Username"><input type="text" value={String(value.username ?? '')} onChange={e => set('username', e.target.value)} className={inputCls} /></F>
        <F label="Password"><input type="password" value={String(value.password ?? '')} onChange={e => set('password', e.target.value)} autoComplete="new-password" className={inputCls} /></F>
        <F label="Tables (comma-separated)"><input type="text" value={String(value.tables_csv ?? '')} onChange={e => set('tables_csv', e.target.value)} placeholder="orders, customers" className={inputCls} /></F>
        {sourceType === 'postgresql' && <F label="CDC Mode"><select value={String(value.cdc_mode ?? 'query')} onChange={e => set('cdc_mode', e.target.value)} className={inputCls}><option value="query">Query (incremental)</option><option value="logical_replication">Logical Replication (CDC)</option><option value="pg_notify">pg_notify</option></select></F>}
      </>}
      {isSnowflake && <>
        <F label="Account"><input type="text" value={String(value.account ?? '')} onChange={e => set('account', e.target.value)} placeholder="org-account" className={inputCls} /></F>
        <F label="Warehouse"><input type="text" value={String(value.warehouse ?? '')} onChange={e => set('warehouse', e.target.value)} className={inputCls} /></F>
        <F label="Database"><input type="text" value={String(value.database ?? '')} onChange={e => set('database', e.target.value)} className={inputCls} /></F>
        <F label="Schema"><input type="text" value={String(value.schema ?? 'PUBLIC')} onChange={e => set('schema', e.target.value)} className={inputCls} /></F>
        <F label="Username"><input type="text" value={String(value.username ?? '')} onChange={e => set('username', e.target.value)} className={inputCls} /></F>
        <F label="Password"><input type="password" value={String(value.password ?? '')} onChange={e => set('password', e.target.value)} autoComplete="new-password" className={inputCls} /></F>
        <F label="Tables (comma-separated)"><input type="text" value={String(value.tables_csv ?? '')} onChange={e => set('tables_csv', e.target.value)} className={inputCls} /></F>
      </>}
      {isMongo && <>
        <F label="Connection URI"><input type="text" value={String(value.uri ?? '')} onChange={e => set('uri', e.target.value)} placeholder="mongodb+srv://..." className={`${inputCls} font-mono`} /></F>
        <F label="Database"><input type="text" value={String(value.database ?? '')} onChange={e => set('database', e.target.value)} className={inputCls} /></F>
        <F label="Collections (comma-separated)"><input type="text" value={String(value.collections_csv ?? '')} onChange={e => set('collections_csv', e.target.value)} className={inputCls} /></F>
      </>}
    </div>
  );
}
