import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function MonitoringFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  return (
    <div className="space-y-4">
      <Field label="Alert Severity Filter" hint="Only trigger for alerts at or above this severity">
        <select
          value={(value.alert_severity_filter as string) ?? ''}
          onChange={(e) => set('alert_severity_filter', e.target.value)}
          className={inputCls}
        >
          <option value="">Any severity (*)</option>
          <option value="info">Info</option>
          <option value="warning">Warning</option>
          <option value="error">Error</option>
          <option value="critical">Critical</option>
          <option value="p1">P1</option>
        </select>
      </Field>
      {triggerType === 'cloudwatch' && (
        <>
          {/* TRG-25: CloudWatch reaches HTTPS only through SNS; the endpoint
              confirms the subscription itself and records when it did. */}
          <div className="rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground space-y-1">
            <p className="font-medium text-foreground">Connect CloudWatch through SNS</p>
            <ol className="list-decimal pl-4 space-y-0.5">
              <li>Create (or pick) an SNS topic and set it as the alarm&apos;s action.</li>
              <li>Add an HTTPS subscription to the topic with this trigger&apos;s webhook URL.</li>
              <li>The subscription is confirmed automatically on the first SNS request.</li>
            </ol>
            <p>Leave the webhook secret blank: SNS messages are verified by their AWS signature.</p>
            {value.sns_subscription_confirmed_at ? (
              <p className="text-emerald-600 dark:text-emerald-400">
                SNS subscription confirmed{' '}
                {new Date(String(value.sns_subscription_confirmed_at)).toLocaleString()}
              </p>
            ) : (
              <p className="text-amber-600 dark:text-amber-400">Awaiting SNS subscription confirmation</p>
            )}
          </div>
          <Field label="CloudWatch Namespace" hint="e.g. AWS/EC2, AWS/Lambda">
            <input
              type="text"
              value={(value.cloudwatch_namespace as string) ?? ''}
              onChange={(e) => set('cloudwatch_namespace', e.target.value)}
              placeholder="AWS/Lambda"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="CloudWatch Metric" hint="Metric name to watch, e.g. Errors, Duration">
            <input
              type="text"
              value={(value.cloudwatch_metric as string) ?? ''}
              onChange={(e) => set('cloudwatch_metric', e.target.value)}
              placeholder="Errors"
              className={`${inputCls} font-mono`}
            />
          </Field>
        </>
      )}
      {triggerType === 'log_pattern' && (
        <>
          <Field label="Log Pattern (regex)" hint="Regular expression to match log lines">
            <input
              type="text"
              value={(value.log_pattern_regex as string) ?? ''}
              onChange={(e) => set('log_pattern_regex', e.target.value)}
              placeholder="ERROR.*database|CRITICAL.*timeout"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Log Stream (optional)" hint="Restrict to a specific log stream name">
            <input
              type="text"
              value={(value.log_stream as string) ?? ''}
              onChange={(e) => set('log_stream', e.target.value)}
              placeholder="app.error"
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'sentry_issue' && (
        <>
          <Field label="Sentry Project (optional)" hint="Filter by Sentry project slug">
            <input
              type="text"
              value={(value.sentry_project as string) ?? ''}
              onChange={(e) => set('sentry_project', e.target.value)}
              placeholder="my-app-backend"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Sentry Environment (optional)" hint="Filter by environment, e.g. production">
            <input
              type="text"
              value={(value.sentry_environment as string) ?? ''}
              onChange={(e) => set('sentry_environment', e.target.value)}
              placeholder="production"
              className={`${inputCls} font-mono`}
            />
          </Field>
        </>
      )}
      {triggerType === 'pagerduty' && (
        <Field label="Check URL" hint="URL to monitor for uptime">
          <input
            type="url"
            value={(value.poll_url as string) ?? ''}
            onChange={(e) => set('poll_url', e.target.value)}
            placeholder="https://api.example.com/health"
            className={inputCls}
          />
        </Field>
      )}
      <Field label="CEL Condition (optional)" hint="Additional filter for alert payload">
        <input
          type="text"
          value={(value.condition_expression as string) ?? ''}
          onChange={(e) => set('condition_expression', e.target.value)}
          placeholder='payload.severity == "critical"'
          className={`${inputCls} font-mono`}
        />
      </Field>
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
