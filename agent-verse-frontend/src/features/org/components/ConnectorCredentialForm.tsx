/**
 * ConnectorCredentialForm — collect a connector's credentials, register it,
 * and validate them against the provider's live API.
 *
 * The fields are rendered from the catalog's `auth_fields` spec, so every
 * connector gets exactly the inputs it needs (a GitHub PAT, a Jira URL +
 * email + token, a Postgres connection string, …). Secret fields render as
 * password inputs and are never echoed back by the server (stored as vault
 * refs). On submit we register the connector, then run the real /test.
 */
import { useState, useCallback, useMemo, useId } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { X, Loader2, CheckCircle2, AlertTriangle, ShieldCheck, ExternalLink } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  connectorsApi,
  isTestPassed,
  type CatalogConnector,
  type ConnectorTestResult,
} from '../connectorsApi';

interface ConnectorCredentialFormProps {
  connector: CatalogConnector;
  onClose:   () => void;
  /** Called after a successful register (+ test), so the marketplace can refetch. */
  onInstalled: () => void;
}

const SECRET_TYPES = new Set(['password', 'secret', 'token']);

export function ConnectorCredentialForm({ connector, onClose, onInstalled }: ConnectorCredentialFormProps) {
  const reduce = useReducedMotion();
  const formId = useId();

  // Seed each field; the URL field defaults to the connector's default URL.
  const [values, setValues] = useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {};
    for (const f of connector.auth_fields) {
      seed[f.key] = f.field_type === 'url' ? connector.default_url : '';
    }
    return seed;
  });
  const [busy, setBusy]       = useState(false);
  const [phase, setPhase]     = useState<'idle' | 'registering' | 'testing'>('idle');
  const [error, setError]     = useState('');
  const [result, setResult]   = useState<ConnectorTestResult | null>(null);

  const setField = useCallback((key: string, v: string) => {
    setValues(prev => ({ ...prev, [key]: v }));
    setError('');
  }, []);

  const missingRequired = useMemo(
    () => connector.auth_fields.some(f => f.required && !values[f.key]?.trim()),
    [connector.auth_fields, values],
  );

  const onSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (missingRequired || busy) return;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      // The URL field (if present) is the connector URL; everything else is auth.
      const urlField = connector.auth_fields.find(f => f.field_type === 'url');
      const url = (urlField ? values[urlField.key] : '') || connector.default_url || 'builtin://';
      const authConfig: Record<string, string> = {};
      for (const f of connector.auth_fields) {
        if (f.field_type === 'url') continue;
        const v = values[f.key]?.trim();
        if (v) authConfig[f.key] = v;
      }

      setPhase('registering');
      const created = await connectorsApi.register({
        name: connector.name,
        url,
        auth_type: connector.auth_type,
        auth_config: authConfig,
        description: connector.description,
      });

      setPhase('testing');
      const test = await connectorsApi.test(created.server_id);
      setResult(test);
      // Registration succeeded regardless of the test outcome (creds are stored);
      // let the marketplace refresh either way so the card shows Connected.
      onInstalled();
      if (isTestPassed(test)) {
        // Auto-close shortly after a clean pass.
        setTimeout(onClose, 1400);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not connect this connector.');
    } finally {
      setBusy(false);
      setPhase('idle');
    }
  }, [busy, missingRequired, connector, values, onInstalled, onClose]);

  const passed = result ? isTestPassed(result) : false;

  return (
    <motion.div
      role="dialog"
      aria-modal="true"
      aria-label={`Connect ${connector.display_name}`}
      initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 8 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.97, y: 8 }}
      transition={{ type: 'spring', stiffness: 380, damping: 30 }}
      className="w-full max-w-sm rounded-2xl bg-[#12151F] border border-[#2D3748] shadow-2xl overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-[#1E2535]">
        <div className="h-9 w-9 rounded-xl bg-[#252B3B] border border-[#2D3748] flex items-center justify-center shrink-0 text-[16px] font-semibold text-[#F1F5F9] uppercase">
          {connector.display_name.slice(0, 1)}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-[14px] font-semibold text-[#F1F5F9] truncate">
            Connect {connector.display_name}
          </h3>
          <p className="text-[11px] text-[#64748B] capitalize">{connector.category.replace(/_/g, ' ')}</p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="p-1.5 rounded-lg text-[#475569] hover:text-[#94A3B8] hover:bg-white/5 transition-colors"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>

      <form onSubmit={onSubmit} className="px-4 py-4 space-y-3">
        <p className="text-[12px] leading-relaxed text-[#94A3B8]">{connector.description}</p>

        {connector.auth_fields.map((f) => {
          const isSecret = SECRET_TYPES.has(f.field_type);
          const inputType = isSecret ? 'password' : f.field_type === 'email' ? 'email' : f.field_type === 'url' ? 'url' : 'text';
          const fid = `${formId}-${f.key}`;
          return (
            <div key={f.key} className="space-y-1">
              <label htmlFor={fid} className="flex items-center gap-1.5 text-[12px] font-medium text-[#CBD5E1]">
                {f.label}
                {f.required && <span className="text-rose-400" aria-hidden>*</span>}
                {isSecret && <ShieldCheck className="h-3 w-3 text-emerald-400/70" aria-label="Stored encrypted" />}
              </label>
              <input
                id={fid}
                type={inputType}
                value={values[f.key] ?? ''}
                onChange={(e) => setField(f.key, e.target.value)}
                placeholder={f.placeholder}
                required={f.required}
                aria-required={f.required}
                autoComplete={isSecret ? 'off' : undefined}
                spellCheck={false}
                className={cn(
                  'w-full px-3 py-2 rounded-lg text-[13px] font-mono',
                  'bg-[#0F1420] border border-[#2D3748] text-[#F1F5F9]',
                  'placeholder:text-[#475569] placeholder:font-sans',
                  'focus:outline-none focus:ring-2 focus:ring-blue-500/60 focus:border-transparent',
                )}
              />
              {f.hint && <p className="text-[11px] text-[#64748B]">{f.hint}</p>}
            </div>
          );
        })}

        {/* Secret-handling reassurance */}
        <p className="flex items-start gap-1.5 text-[11px] text-[#64748B]">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-emerald-400/60 mt-px" aria-hidden />
          Credentials are stored encrypted as vault references and validated against {connector.display_name}&apos;s live API — never shown back.
        </p>

        {/* Test result */}
        {result && (
          <div
            role="status"
            className={cn(
              'flex items-start gap-2 rounded-lg px-3 py-2 text-[12px]',
              passed
                ? 'bg-emerald-500/10 ring-1 ring-emerald-500/20 text-emerald-300'
                : 'bg-rose-500/10 ring-1 ring-rose-500/20 text-rose-300',
            )}
          >
            {passed
              ? <CheckCircle2 className="h-4 w-4 shrink-0 mt-px" aria-hidden />
              : <AlertTriangle className="h-4 w-4 shrink-0 mt-px" aria-hidden />}
            <span className="min-w-0 whitespace-pre-wrap break-words">
              {passed
                ? (result.detail || 'Connected and verified.')
                : (result.error || 'Credentials could not be verified.')}
              {typeof result.latency_ms === 'number' && (
                <span className="text-[#64748B]"> · {result.latency_ms}ms</span>
              )}
            </span>
          </div>
        )}

        {error && (
          <p role="alert" className="text-[12px] text-rose-400">{error}</p>
        )}

        <div className="flex items-center gap-2 pt-1">
          <button
            type="submit"
            disabled={busy || missingRequired}
            className={cn(
              'flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-[13px] font-semibold',
              'bg-blue-600 hover:bg-blue-500 text-white',
              'disabled:opacity-50 disabled:cursor-not-allowed',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
              'active:scale-[0.98] transition-[background-color,transform] duration-150',
            )}
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
            {phase === 'registering' ? 'Saving…' : phase === 'testing' ? 'Verifying…' : passed ? 'Connected' : 'Connect & verify'}
          </button>
        </div>

        {connector.auth_fields.some(f => f.field_type === 'password' || f.field_type === 'token') && (
          <a
            href={docsUrlFor(connector.name)}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-center gap-1 text-[11px] text-[#64748B] hover:text-[#94A3B8] transition-colors"
          >
            Where do I find my credentials? <ExternalLink className="h-3 w-3" aria-hidden />
          </a>
        )}
      </form>
    </motion.div>
  );
}

/** Best-effort deep link to where a provider issues API tokens. */
function docsUrlFor(name: string): string {
  const links: Record<string, string> = {
    github: 'https://github.com/settings/tokens',
    gitlab: 'https://gitlab.com/-/user_settings/personal_access_tokens',
    slack:  'https://api.slack.com/apps',
    notion: 'https://www.notion.so/my-integrations',
    linear: 'https://linear.app/settings/api',
    jira:   'https://id.atlassian.com/manage-profile/security/api-tokens',
    stripe: 'https://dashboard.stripe.com/apikeys',
    openai: 'https://platform.openai.com/api-keys',
  };
  return links[name.toLowerCase()] ?? `https://www.google.com/search?q=${encodeURIComponent(name + ' create API token')}`;
}
