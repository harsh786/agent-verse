import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ApiError,
  tenantsApi,
  type SmtpTestResult,
  type SmtpTlsMode,
  type TenantEmailSettings,
  type TenantSmtpConfigInput,
} from '@/lib/api/client';

/**
 * a02-F036-02 — how the agent email tool sends mail for this workspace.
 *
 * - Recipient allowlist (optional): empty = any recipient; otherwise a message with
 *   any recipient outside it is refused (nothing is sent).
 * - Your own SMTP sender: when set, agent email goes through it; otherwise through
 *   the AgentVerse platform relay. System mail (invites, password resets, alerts)
 *   always uses the platform relay. The password / API key is write-only.
 */

const QUERY_KEY = ['tenant', 'email-settings'] as const;

const DEFAULT_PORT: Record<SmtpTlsMode, number> = { starttls: 587, tls: 465, none: 25 };

interface SmtpForm {
  host: string;
  port: string;
  tls_mode: SmtpTlsMode;
  username: string;
  from_address: string;
  secret: string;
}

const EMPTY_FORM: SmtpForm = {
  host: '',
  port: '587',
  tls_mode: 'starttls',
  username: '',
  from_address: '',
  secret: '',
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formFrom(settings: TenantEmailSettings | undefined): SmtpForm {
  const smtp = settings?.smtp;
  if (!smtp) return EMPTY_FORM;
  return {
    host: smtp.host,
    port: String(smtp.port),
    tls_mode: smtp.tls_mode,
    username: smtp.username,
    from_address: smtp.from_address,
    secret: '',
  };
}

function toInput(form: SmtpForm): TenantSmtpConfigInput {
  const input: TenantSmtpConfigInput = {
    host: form.host.trim().toLowerCase(),
    port: Number(form.port),
    tls_mode: form.tls_mode,
    username: form.username.trim(),
    from_address: form.from_address.trim(),
  };
  // Blank = keep the stored secret (the backend only reuses it for the same server).
  if (form.secret) input.secret = form.secret;
  return input;
}

function describeTest(result: SmtpTestResult): string {
  if (result.ok) return result.message === 'connected' ? 'Connected and authenticated.' : 'Test message sent.';
  const code = result.smtp_code ? ` (SMTP ${result.smtp_code})` : '';
  return `Failed at ${result.stage}: ${result.message}${code}`;
}

const inputClass =
  'w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/40';

function AllowlistEditor({ settings }: { settings: TenantEmailSettings }) {
  const qc = useQueryClient();
  const [text, setText] = useState(settings.recipient_allowlist.join('\n'));
  useEffect(() => setText(settings.recipient_allowlist.join('\n')), [settings.recipient_allowlist]);
  const save = useMutation({
    mutationFn: (entries: string[]) => tenantsApi.setEmailAllowlist(entries),
    onSuccess: (data) => qc.setQueryData(QUERY_KEY, data),
  });
  const entries = text
    .split(/[\n,]/)
    .map((e) => e.trim())
    .filter(Boolean);

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold">Recipient allowlist</h4>
      <p className="text-sm text-muted-foreground">
        One per line: an address (<code>alice@example.com</code>), a domain (<code>example.com</code>)
        or its subdomains (<code>*.example.com</code>). Leave empty to allow any recipient. When set,
        a message with any recipient outside the list is refused and nothing is sent.
      </p>
      <label htmlFor="email-allowlist" className="sr-only">
        Recipient allowlist
      </label>
      <textarea
        id="email-allowlist"
        rows={5}
        value={text}
        onChange={(e) => setText(e.target.value)}
        className={`${inputClass} font-mono`}
        placeholder="example.com"
      />
      {save.error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {errorText(save.error)}
        </p>
      )}
      <button
        type="button"
        onClick={() => save.mutate(entries)}
        disabled={save.isPending}
        className="px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
      >
        {save.isPending ? 'Saving…' : 'Save allowlist'}
      </button>
      <span className="ml-3 text-xs text-muted-foreground">
        {settings.recipient_allowlist.length === 0
          ? 'Not restricted'
          : `${settings.recipient_allowlist.length} entr${settings.recipient_allowlist.length === 1 ? 'y' : 'ies'}`}
      </span>
    </div>
  );
}

function SmtpEditor({ settings }: { settings: TenantEmailSettings }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<SmtpForm>(() => formFrom(settings));
  const [testResult, setTestResult] = useState<SmtpTestResult | null>(null);
  useEffect(() => setForm(formFrom(settings)), [settings]);

  const save = useMutation({
    mutationFn: (input: TenantSmtpConfigInput) => tenantsApi.setEmailSmtp(input),
    onSuccess: (data) => {
      qc.setQueryData(QUERY_KEY, data);
      setForm(formFrom(data)); // the secret field is cleared; it is never read back
    },
  });
  const remove = useMutation({
    mutationFn: () => tenantsApi.deleteEmailSmtp(),
    onSuccess: (data) => {
      qc.setQueryData(QUERY_KEY, data);
      setTestResult(null);
    },
  });
  const test = useMutation({
    mutationFn: (input: TenantSmtpConfigInput) => tenantsApi.testEmailSmtp({ config: input }),
    onSuccess: (data) => setTestResult(data),
    onMutate: () => setTestResult(null),
  });

  const set = (field: keyof SmtpForm) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [field]: e.target.value }));
  const onTlsChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const mode = e.target.value as SmtpTlsMode;
    setForm((f) => ({ ...f, tls_mode: mode, port: String(DEFAULT_PORT[mode]) }));
  };
  const complete = form.host.trim() !== '' && form.from_address.trim() !== '' && Number(form.port) > 0;
  const secretStored = settings.smtp?.secret_set === true;
  const error = save.error ?? remove.error ?? test.error;

  return (
    <div className="space-y-3">
      <h4 className="text-sm font-semibold">Your SMTP sender</h4>
      <p className="text-sm text-muted-foreground">
        Agent email is sent through your own mail server when this is set; otherwise through the
        AgentVerse platform relay. System mail (invites, password resets, alerts) always uses the
        platform relay.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="text-sm space-y-1">
          <span>SMTP host</span>
          <input className={inputClass} value={form.host} onChange={set('host')} placeholder="smtp.example.com" />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm space-y-1">
            <span>Encryption</span>
            <select className={inputClass} value={form.tls_mode} onChange={onTlsChange}>
              <option value="starttls">STARTTLS</option>
              <option value="tls">TLS (implicit)</option>
              <option value="none">None</option>
            </select>
          </label>
          <label className="text-sm space-y-1">
            <span>Port</span>
            <input className={inputClass} inputMode="numeric" value={form.port} onChange={set('port')} />
          </label>
        </div>
        <label className="text-sm space-y-1">
          <span>Username</span>
          <input className={inputClass} value={form.username} onChange={set('username')} autoComplete="off" />
        </label>
        <label className="text-sm space-y-1">
          <span>Password or API key</span>
          <input
            className={inputClass}
            type="password"
            value={form.secret}
            onChange={set('secret')}
            autoComplete="new-password"
            placeholder={secretStored ? `${settings.smtp?.secret_masked ?? '********'} (stored — leave blank to keep)` : ''}
          />
        </label>
        <label className="text-sm space-y-1 sm:col-span-2">
          <span>From address</span>
          <input
            className={inputClass}
            type="email"
            value={form.from_address}
            onChange={set('from_address')}
            placeholder="agents@example.com"
          />
        </label>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {errorText(error)}
        </p>
      )}
      {testResult && (
        <p
          role="status"
          className={`text-sm ${testResult.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}
        >
          {describeTest(testResult)}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => save.mutate(toInput(form))}
          disabled={!complete || save.isPending}
          className="px-4 py-2 text-sm rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {save.isPending ? 'Saving…' : 'Save SMTP sender'}
        </button>
        <button
          type="button"
          onClick={() => test.mutate(toInput(form))}
          disabled={!complete || test.isPending}
          className="px-4 py-2 text-sm rounded-lg border border-border hover:bg-muted/60 disabled:opacity-50"
        >
          {test.isPending ? 'Testing…' : 'Test connection'}
        </button>
        {settings.smtp && (
          <button
            type="button"
            onClick={() => remove.mutate()}
            disabled={remove.isPending}
            className="px-4 py-2 text-sm rounded-lg text-destructive hover:underline disabled:opacity-50"
          >
            Remove (use the platform relay)
          </button>
        )}
      </div>
    </div>
  );
}

export function EmailSettings() {
  const query = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => tenantsApi.getEmailSettings(),
    retry: false,
  });

  if (query.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading email settings…</p>;
  }
  if (query.error) {
    const forbidden = query.error instanceof ApiError && query.error.status === 403;
    return (
      <p role="alert" className="text-sm text-red-600 dark:text-red-400">
        {forbidden ? 'Only workspace admins can view and change email settings.' : errorText(query.error)}
      </p>
    );
  }
  const settings = query.data;
  if (!settings) return null;

  return (
    <div className="space-y-6">
      <p className="text-sm">
        Agent email is sent through:{' '}
        <strong data-testid="email-relay">
          {settings.relay === 'tenant' ? 'your SMTP server' : 'the AgentVerse platform relay'}
        </strong>
      </p>
      <AllowlistEditor settings={settings} />
      <SmtpEditor settings={settings} />
    </div>
  );
}
