import type { TriggerType } from '../../types';

interface FamilyFormProps {
  triggerType: TriggerType;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
}

export function ConversationalFamilyForm({ triggerType, value, onChange }: FamilyFormProps) {
  function set(key: string, val: unknown) {
    onChange({ ...value, [key]: val });
  }

  return (
    <div className="space-y-4">
      {(triggerType === 'chat_command') && (
        <Field label="Command Pattern" hint="e.g. /run or /deploy.*">
          <input
            type="text"
            value={(value.command_pattern as string) ?? ''}
            onChange={(e) => set('command_pattern', e.target.value)}
            placeholder="/run"
            className={inputCls}
          />
        </Field>
      )}
      {(triggerType === 'chat_keyword' || triggerType === 'chat_mention') && (
        <Field label="Keyword Pattern" hint="Regex pattern or keyword to match">
          <input
            type="text"
            value={(value.keyword_pattern as string) ?? ''}
            onChange={(e) => set('keyword_pattern', e.target.value)}
            placeholder="urgent|alert|incident"
            className={inputCls}
          />
        </Field>
      )}
      {(triggerType === 'slack_event' || triggerType === 'chat_command' || triggerType === 'chat_keyword' || triggerType === 'chat_mention') && (
        <Field label="Channel ID (optional)" hint="Restrict to a specific Slack channel">
          <input
            type="text"
            value={(value.channel_id as string) ?? ''}
            onChange={(e) => set('channel_id', e.target.value)}
            placeholder="C1234ABCD"
            className={`${inputCls} font-mono`}
          />
        </Field>
      )}
      {(triggerType === 'email_intent' || triggerType === 'email_arrival') && (
        <>
          <Field label="Sender Filter (optional)" hint="Email or domain to filter by">
            <input
              type="text"
              value={(value.email_sender_filter as string) ?? ''}
              onChange={(e) => set('email_sender_filter', e.target.value)}
              placeholder="@company.com"
              className={inputCls}
            />
          </Field>
          <Field label="Subject Pattern" hint="Regex pattern for subject line">
            <input
              type="text"
              value={(value.email_subject_pattern as string) ?? ''}
              onChange={(e) => set('email_subject_pattern', e.target.value)}
              placeholder="urgent|invoice|.*paid"
              className={inputCls}
            />
          </Field>
        </>
      )}
      {triggerType === 'sms_inbound' && (
        <Field label="Phone Number Filter (optional)">
          <input
            type="tel"
            value={(value.phone_number_filter as string) ?? ''}
            onChange={(e) => set('phone_number_filter', e.target.value)}
            placeholder="+1234567890"
            className={inputCls}
          />
        </Field>
      )}
      {triggerType === 'voice_transcript' && (
        <Field label="Language" hint="BCP-47 language code">
          <input
            type="text"
            value={(value.voice_language as string) ?? 'en-US'}
            onChange={(e) => set('voice_language', e.target.value)}
            placeholder="en-US"
            className={inputCls}
          />
        </Field>
      )}
      {triggerType === 'meeting_ended' && (
        <Field label="Meeting Platform">
          <select
            value={(value.meeting_platform as string) ?? ''}
            onChange={(e) => set('meeting_platform', e.target.value)}
            className={inputCls}
          >
            <option value="">Any platform</option>
            <option value="zoom">Zoom</option>
            <option value="teams">Microsoft Teams</option>
            <option value="google_meet">Google Meet</option>
          </select>
        </Field>
      )}
      {triggerType === 'form_submission' && (
        <Field label="Form ID" hint="The form identifier from your form builder">
          <input
            type="text"
            value={(value.form_id as string) ?? ''}
            onChange={(e) => set('form_id', e.target.value)}
            placeholder="contact-form-001"
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
