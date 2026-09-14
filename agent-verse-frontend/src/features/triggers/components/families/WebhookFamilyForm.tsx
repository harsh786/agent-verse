import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function WebhookFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  const allowedApiKeys = Array.isArray(value.allowed_api_keys)
    ? (value.allowed_api_keys as string[])
    : [];

  return (
    <div className="space-y-4">
      {triggerType === 'webhook' && (
        <Field label="Endpoint Name" hint="Used to generate the webhook URL path">
          <input
            type="text"
            value={(value.description as string) ?? ''}
            onChange={(e) => set('description', e.target.value)}
            placeholder="my-webhook"
            className={inputCls}
          />
        </Field>
      )}
      <Field
        label="Webhook Secret"
        hint="Used for HMAC-SHA256 signature verification. Leave blank to disable verification."
      >
        <input
          type="password"
          value={(value.webhook_signature_secret as string) ?? ''}
          onChange={(e) => set('webhook_signature_secret', e.target.value)}
          placeholder="whsec_…"
          className={inputCls}
          autoComplete="new-password"
        />
      </Field>
      <Field
        label="Allowed API Keys"
        hint="Comma-separated keys accepted on inbound requests (blank = signature only)"
      >
        <input
          type="text"
          value={allowedApiKeys.join(', ')}
          onChange={(e) =>
            set(
              'allowed_api_keys',
              e.target.value
                .split(',')
                .map((k) => k.trim())
                .filter(Boolean),
            )
          }
          placeholder="key_live_abc, key_live_def"
          className={inputCls}
        />
      </Field>
      {triggerType === 'jira_webhook' && (
        <Field label="Jira Project Filter" hint="Project key to filter by, e.g. OPS (blank = all)">
          <input
            type="text"
            value={(value.jira_project_filter as string) ?? ''}
            onChange={(e) => set('jira_project_filter', e.target.value)}
            placeholder="OPS"
            className={inputCls}
          />
        </Field>
      )}
      {triggerType === 'salesforce_event' && (
        <Field label="Salesforce Object" hint="SObject to watch, e.g. Opportunity, Case">
          <input
            type="text"
            value={(value.salesforce_object as string) ?? ''}
            onChange={(e) => set('salesforce_object', e.target.value)}
            placeholder="Opportunity"
            className={inputCls}
          />
        </Field>
      )}
      {triggerType === 'event' && (
        <>
          <Field label="Event Channel" hint="Internal event channel/topic name to subscribe to">
            <input
              type="text"
              value={(value.event_channel as string) ?? ''}
              onChange={(e) => set('event_channel', e.target.value)}
              placeholder="orders.created"
              className={inputCls}
            />
          </Field>
          <Field label="Event Filter (JSONPath)" hint="Optional JSONPath filter on the event body, e.g. $.type">
            <input
              type="text"
              value={(value.event_filter as string) ?? ''}
              onChange={(e) => set('event_filter', e.target.value)}
              placeholder="$.type"
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'github_webhook' && (
        <Field label="Filter Event Type" hint="e.g. push, pull_request, issues (blank = all)">
          <input
            type="text"
            value={(value.github_event_filter as string) ?? ''}
            onChange={(e) => set('github_event_filter', e.target.value)}
            placeholder="push"
            className={inputCls}
          />
        </Field>
      )}
      {triggerType === 'stripe_webhook' && (
        <Field label="Filter Event Type" hint="e.g. payment_intent.succeeded">
          <input
            type="text"
            value={(value.stripe_event_filter as string) ?? ''}
            onChange={(e) => set('stripe_event_filter', e.target.value)}
            placeholder="payment_intent.succeeded"
            className={inputCls}
          />
        </Field>
      )}
      <Field label="Condition CEL" hint="Optional filter expression, e.g. payload.action == 'opened'">
        <input
          type="text"
          value={(value.condition as string) ?? ''}
          onChange={(e) => set('condition', e.target.value)}
          placeholder="payload.ref == 'refs/heads/main'"
          className={inputCls}
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

const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring font-mono';
