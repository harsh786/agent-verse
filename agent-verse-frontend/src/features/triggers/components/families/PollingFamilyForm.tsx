import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function PollingFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  return (
    <div className="space-y-4">
      {triggerType === 'graphql_subscription' && (
        <>
          <Field label="GraphQL Endpoint (WebSocket)">
            <input
              type="text"
              value={(value.graphql_endpoint as string) ?? ''}
              onChange={(e) => set('graphql_endpoint', e.target.value)}
              placeholder="wss://api.example.com/graphql"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Subscription Query">
            <textarea
              rows={4}
              value={(value.graphql_subscription_query as string) ?? ''}
              onChange={(e) => set('graphql_subscription_query', e.target.value)}
              placeholder="subscription { onOrderCreated { id status } }"
              className={`${inputCls} font-mono resize-y`}
            />
          </Field>
        </>
      )}
      {triggerType === 'kafka_message' && (
        <Field label="Kafka Topic">
          <input
            type="text"
            value={(value.event_channel as string) ?? ''}
            onChange={(e) => set('event_channel', e.target.value)}
            placeholder="orders.created"
            className={`${inputCls} font-mono`}
          />
        </Field>
      )}
      {triggerType === 'price_movement' && (
        <>
          <Field label="Symbol" hint="e.g. BTC, ETH, AAPL">
            <input
              type="text"
              value={(value.price_symbol as string) ?? ''}
              onChange={(e) => set('price_symbol', e.target.value.toUpperCase())}
              placeholder="BTC"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Threshold Price">
            <input
              type="number"
              step="any"
              min={0}
              value={(value.price_threshold as number) ?? 0}
              onChange={(e) => set('price_threshold', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
          <Field label="Direction">
            <select
              value={(value.price_direction as string) ?? 'above'}
              onChange={(e) => set('price_direction', e.target.value)}
              className={inputCls}
            >
              <option value="above">Crosses above</option>
              <option value="below">Falls below</option>
              <option value="either">Either direction</option>
            </select>
          </Field>
        </>
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
