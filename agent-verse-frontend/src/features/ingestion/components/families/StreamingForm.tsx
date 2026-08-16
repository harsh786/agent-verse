interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="block text-sm font-medium mb-1">{label}</label>{children}</div>;
}
const set = (value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, key: string, val: unknown) => onChange({ ...value, [key]: val });
export function StreamingForm({ sourceType, value, onChange }: FormProps) {
  const s = (k: string, v: unknown) => set(value, onChange, k, v);
  return (
    <div className="space-y-3">
      {(sourceType === 'kafka' || sourceType === 'redpanda') && (
        <Field label="Bootstrap Servers"><input type="text" value={String(value.bootstrap_servers ?? '')} onChange={e => s('bootstrap_servers', e.target.value)} placeholder="localhost:9092" className={inputCls} /></Field>
      )}
      <Field label="Topic"><input type="text" value={String(value.topic ?? '')} onChange={e => s('topic', e.target.value)} placeholder="my-topic" className={inputCls} /></Field>
      {sourceType === 'kinesis' && <Field label="Stream Name"><input type="text" value={String(value.stream_name ?? '')} onChange={e => s('stream_name', e.target.value)} className={inputCls} /></Field>}
      {sourceType === 'pubsub' && <Field label="Subscription"><input type="text" value={String(value.subscription ?? '')} onChange={e => s('subscription', e.target.value)} className={inputCls} /></Field>}
      {sourceType === 'eventbridge' && <Field label="Event Bus"><input type="text" value={String(value.event_bus ?? '')} onChange={e => s('event_bus', e.target.value)} className={inputCls} /></Field>}
    </div>
  );
}
export { inputCls };
