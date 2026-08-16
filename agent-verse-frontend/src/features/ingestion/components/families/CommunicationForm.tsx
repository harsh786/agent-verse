interface FormProps { sourceType: string; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void; }
const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><label className="block text-sm font-medium mb-1">{label}</label>{children}</div>;
}
const set = (value: Record<string, unknown>, onChange: (v: Record<string, unknown>) => void, key: string, val: unknown) => onChange({ ...value, [key]: val });
export function CommunicationForm({ sourceType, value, onChange }: FormProps) {
  const s = (k: string, v: unknown) => set(value, onChange, k, v);
  return (
    <div className="space-y-3">
      {sourceType === 'slack' && <>
        <Field label="Bot Token"><input type="password" value={String(value.bot_token ?? '')} onChange={e => s('bot_token', e.target.value)} placeholder="xoxb-..." className={inputCls} autoComplete="new-password" /></Field>
        <Field label="Channel ID"><input type="text" value={String(value.channel_id ?? '')} onChange={e => s('channel_id', e.target.value)} placeholder="C01234567" className={inputCls} /></Field>
      </>}
      {sourceType === 'email' && <>
        <Field label="IMAP Host"><input type="text" value={String(value.imap_host ?? '')} onChange={e => s('imap_host', e.target.value)} placeholder="imap.gmail.com" className={inputCls} /></Field>
        <Field label="Username"><input type="text" value={String(value.username ?? '')} onChange={e => s('username', e.target.value)} className={inputCls} /></Field>
        <Field label="Password"><input type="password" value={String(value.password ?? '')} onChange={e => s('password', e.target.value)} className={inputCls} autoComplete="new-password" /></Field>
      </>}
      {sourceType === 'teams' && <Field label="Webhook URL"><input type="text" value={String(value.webhook_url ?? '')} onChange={e => s('webhook_url', e.target.value)} placeholder="https://..." className={inputCls} /></Field>}
      {sourceType === 'discord' && <Field label="Bot Token"><input type="password" value={String(value.bot_token ?? '')} onChange={e => s('bot_token', e.target.value)} className={inputCls} autoComplete="new-password" /></Field>}
    </div>
  );
}
