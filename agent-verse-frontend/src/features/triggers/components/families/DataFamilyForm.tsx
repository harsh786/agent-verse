import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function DataFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  return (
    <div className="space-y-4">
      {triggerType === 'db_row_change' && (
        <>
          <Field label="Database Table">
            <input
              type="text"
              value={(value.db_table as string) ?? ''}
              onChange={(e) => set('db_table', e.target.value)}
              placeholder="orders"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Operation">
            <select
              value={(value.db_operation as string) ?? ''}
              onChange={(e) => set('db_operation', e.target.value)}
              className={inputCls}
            >
              <option value="">Any operation</option>
              <option value="INSERT">INSERT</option>
              <option value="UPDATE">UPDATE</option>
              <option value="DELETE">DELETE</option>
            </select>
          </Field>
          <Field label="Row Filter (JSON)" hint='e.g. {"status": "pending"}'>
            <input
              type="text"
              value={(value.db_filter as string) ?? ''}
              onChange={(e) => set('db_filter', e.target.value)}
              placeholder='{"status": "pending"}'
              className={`${inputCls} font-mono`}
            />
          </Field>
        </>
      )}
      {triggerType === 's3_event' && (
        <>
          <Field label="S3 Bucket">
            <input
              type="text"
              value={(value.s3_bucket as string) ?? ''}
              onChange={(e) => set('s3_bucket', e.target.value)}
              placeholder="my-data-bucket"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Key Prefix (optional)" hint="Only trigger for keys starting with this prefix">
            <input
              type="text"
              value={(value.s3_prefix as string) ?? ''}
              onChange={(e) => set('s3_prefix', e.target.value)}
              placeholder="reports/"
              className={`${inputCls} font-mono`}
            />
          </Field>
        </>
      )}
      {triggerType === 'api_poll' && (
        <>
          <Field label="Poll URL">
            <input
              type="url"
              value={(value.poll_url as string) ?? ''}
              onChange={(e) => set('poll_url', e.target.value)}
              placeholder="https://api.example.com/status"
              className={inputCls}
            />
          </Field>
          <Field label="HTTP Method">
            <select
              value={(value.poll_method as string) ?? 'GET'}
              onChange={(e) => set('poll_method', e.target.value)}
              className={inputCls}
            >
              <option value="GET">GET</option>
              <option value="POST">POST</option>
            </select>
          </Field>
          <Field label="JSONPath Expression" hint='e.g. $.results.count to extract a value'>
            <input
              type="text"
              value={(value.poll_jsonpath as string) ?? ''}
              onChange={(e) => set('poll_jsonpath', e.target.value)}
              placeholder="$.status"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Expected Value (triggers when matched)">
            <input
              type="text"
              value={(value.poll_expected_value as string) ?? ''}
              onChange={(e) => set('poll_expected_value', e.target.value)}
              placeholder="complete"
              className={inputCls}
            />
          </Field>
          <Field label="Poll Interval (seconds)">
            <input
              type="number"
              min={60}
              value={(value.poll_interval_seconds as number) ?? 300}
              onChange={(e) => set('poll_interval_seconds', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'rss_feed' && (
        <Field label="RSS/Atom Feed URL">
          <input
            type="url"
            value={(value.rss_url as string) ?? ''}
            onChange={(e) => set('rss_url', e.target.value)}
            placeholder="https://feeds.example.com/rss.xml"
            className={inputCls}
          />
        </Field>
      )}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium mb-1">{label}</label>
      {hint && <p className="text-xs text-muted-foreground mb-1.5">{hint}</p>}
      {children}
    </div>
  );
}

const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
