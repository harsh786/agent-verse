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

  return (
    <div className="space-y-4">
      {triggerType === 'custom_webhook' && (
        <Field label="Endpoint Name" hint="Used to generate the webhook URL path">
          <input
            type="text"
            value={(value.webhook_endpoint_name as string) ?? ''}
            onChange={(e) => set('webhook_endpoint_name', e.target.value)}
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
          value={(value.webhook_secret as string) ?? ''}
          onChange={(e) => set('webhook_secret', e.target.value)}
          placeholder="whsec_…"
          className={inputCls}
          autoComplete="new-password"
        />
      </Field>
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
          value={(value.condition_cel as string) ?? ''}
          onChange={(e) => set('condition_cel', e.target.value)}
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
