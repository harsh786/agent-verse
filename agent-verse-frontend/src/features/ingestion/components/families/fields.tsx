import { useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';

/** Shared building blocks for Source connection forms. */

export const inputCls = 'w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring';

export function Field({ label, hint, htmlFor, children }: { label: string; hint?: string; htmlFor?: string; children: ReactNode }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="block text-sm font-medium mb-1">{label}</label>
      {hint && <p className="text-xs text-muted-foreground mb-1.5">{hint}</p>}
      {children}
    </div>
  );
}

const MASKED: CSSProperties = { WebkitTextSecurity: 'disc' } as CSSProperties;

/**
 * A masked single-line secret (password, token, connection string with an
 * embedded password). The backend returns stored secrets as "********"; sending
 * that value back unchanged keeps the stored secret.
 */
export function SecretInput({ id, label, value, onChange, placeholder, hint }: {
  id: string; label: string; value: unknown; onChange: (v: string) => void; placeholder?: string; hint?: string;
}) {
  const [shown, setShown] = useState(false);
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <div className="flex gap-2">
        <input
          id={id}
          type={shown ? 'text' : 'password'}
          value={String(value ?? '')}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete="new-password"
          spellCheck={false}
          className={`${inputCls} font-mono`}
        />
        <button type="button" onClick={() => setShown(s => !s)} aria-label={`${shown ? 'Hide' : 'Show'} ${label}`}
          className="rounded-lg border border-border px-2 text-xs hover:bg-muted">
          {shown ? 'Hide' : 'Show'}
        </button>
      </div>
    </Field>
  );
}

/** A masked multi-line secret, e.g. a PEM private key (a password input would drop the newlines). */
export function SecretTextarea({ id, label, value, onChange, placeholder, hint }: {
  id: string; label: string; value: unknown; onChange: (v: string) => void; placeholder?: string; hint?: string;
}) {
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <textarea
        id={id}
        rows={4}
        value={String(value ?? '')}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        data-secret="true"
        style={MASKED}
        className={`${inputCls} font-mono resize-y`}
      />
    </Field>
  );
}

/** Plain multi-line PEM input (CA bundle, client certificate — public material). */
export function PemTextarea({ id, label, value, onChange, hint }: {
  id: string; label: string; value: unknown; onChange: (v: string) => void; hint?: string;
}) {
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <textarea id={id} rows={4} value={String(value ?? '')} onChange={e => onChange(e.target.value)}
        placeholder="-----BEGIN CERTIFICATE-----" spellCheck={false} className={`${inputCls} font-mono resize-y`} />
    </Field>
  );
}

export function Checkbox({ id, label, checked, onChange, hint }: {
  id: string; label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium">
        <input id={id} type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
        {label}
      </label>
      {hint && <p className="text-xs text-muted-foreground mt-1">{hint}</p>}
    </div>
  );
}

/**
 * TLS settings shared by database connectors: server verification against a
 * custom CA, and a client certificate + key for mutual TLS. Field names match
 * the backend connectors (tls, tls_ca_pem, tls_client_cert, tls_client_private_key).
 */
export function TlsFields({ prefix, value, set }: {
  prefix: string; value: Record<string, unknown>; set: (k: string, v: unknown) => void;
}) {
  const enabled = Boolean(value.tls);
  return (
    <div className="space-y-3">
      <Checkbox id={`${prefix}-tls`} label="Use TLS" checked={enabled} onChange={v => set('tls', v)} />
      {enabled && <>
        <PemTextarea id={`${prefix}-tls-ca`} label="CA certificate (PEM, optional)" hint="Verify the server against this CA instead of the system trust store."
          value={value.tls_ca_pem} onChange={v => set('tls_ca_pem', v)} />
        <PemTextarea id={`${prefix}-tls-cert`} label="Client certificate (PEM, mutual TLS)" value={value.tls_client_cert} onChange={v => set('tls_client_cert', v)} />
        <SecretTextarea id={`${prefix}-tls-key`} label="Client private key (PEM, mutual TLS)" placeholder="-----BEGIN PRIVATE KEY-----"
          value={value.tls_client_private_key} onChange={v => set('tls_client_private_key', v)} />
        <SecretInput id={`${prefix}-tls-key-pass`} label="Client key passphrase (optional)" value={value.tls_client_key_password} onChange={v => set('tls_client_key_password', v)} />
        <Checkbox id={`${prefix}-tls-insecure`} label="Skip server certificate verification (not recommended)"
          checked={Boolean(value.tls_allow_invalid_certificates)} onChange={v => set('tls_allow_invalid_certificates', v)} />
      </>}
    </div>
  );
}
