import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function IoTFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  return (
    <div className="space-y-4">
      {triggerType === 'mqtt' && (
        <>
          <Field label="MQTT Broker URL">
            <input
              type="text"
              value={(value.mqtt_broker_url as string) ?? ''}
              onChange={(e) => set('mqtt_broker_url', e.target.value)}
              placeholder="mqtt://broker.example.com:1883"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Topic Pattern" hint="Use + for single-level and # for multi-level wildcards">
            <input
              type="text"
              value={(value.mqtt_topic as string) ?? ''}
              onChange={(e) => set('mqtt_topic', e.target.value)}
              placeholder="sensors/+/temperature"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="QoS Level">
            <select
              value={(value.mqtt_qos as number) ?? 0}
              onChange={(e) => set('mqtt_qos', Number(e.target.value))}
              className={inputCls}
            >
              <option value={0}>0 — At most once</option>
              <option value={1}>1 — At least once</option>
              <option value={2}>2 — Exactly once</option>
            </select>
          </Field>
        </>
      )}
      {triggerType === 'geofence' && (
        <>
          <Field label="Geofence Action">
            <select
              value={(value.geofence_action as string) ?? 'enter'}
              onChange={(e) => set('geofence_action', e.target.value)}
              className={inputCls}
            >
              <option value="enter">On Enter</option>
              <option value="exit">On Exit</option>
              <option value="both">Both Enter & Exit</option>
            </select>
          </Field>
          <Field
            label="Geofence Polygon (JSON)"
            hint='Array of [lat, lng] pairs defining the polygon boundary'
          >
            <textarea
              rows={3}
              value={
                value.geofence_polygon
                  ? JSON.stringify(value.geofence_polygon)
                  : ''
              }
              onChange={(e) => {
                try {
                  set('geofence_polygon', JSON.parse(e.target.value));
                } catch {
                  set('geofence_polygon', e.target.value);
                }
              }}
              placeholder='[[51.5, -0.1], [51.5, -0.05], [51.51, -0.05], [51.51, -0.1]]'
              className={`${inputCls} font-mono resize-y`}
            />
          </Field>
        </>
      )}
      {triggerType === 'sensor_threshold' && (
        <>
          <Field label="Device ID (optional)">
            <input
              type="text"
              value={(value.sensor_device_id as string) ?? ''}
              onChange={(e) => set('sensor_device_id', e.target.value)}
              placeholder="device-001"
              className={`${inputCls} font-mono`}
            />
          </Field>
          <Field label="Metric Name" hint="e.g. temperature, humidity, pressure">
            <input
              type="text"
              value={(value.sensor_metric as string) ?? ''}
              onChange={(e) => set('sensor_metric', e.target.value)}
              placeholder="temperature"
              className={inputCls}
            />
          </Field>
          <Field label="Threshold Value">
            <input
              type="number"
              step="any"
              value={(value.sensor_threshold as number) ?? 80}
              onChange={(e) => set('sensor_threshold', Number(e.target.value))}
              className={inputCls}
            />
          </Field>
          <Field label="Comparison">
            <select
              value={(value.sensor_comparison as string) ?? '>'}
              onChange={(e) => set('sensor_comparison', e.target.value)}
              className={inputCls}
            >
              <option value=">">Greater than (&gt;)</option>
              <option value=">=">Greater than or equal (≥)</option>
              <option value="<">Less than (&lt;)</option>
              <option value="<=">Less than or equal (≤)</option>
              <option value="==">Equal to (=)</option>
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
