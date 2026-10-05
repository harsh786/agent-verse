import { useState } from 'react';
import { friendlyConnectionError } from '@/lib/friendlyError';

/**
 * A connection/driver error as a short reason, with the raw text — sanitised:
 * no credentials, no hosts — behind a "Details" toggle.
 */
export function FriendlyErrorMessage({
  error,
  fallback,
  prefix,
  className = 'text-xs text-destructive',
  ...rest
}: {
  error: unknown;
  fallback?: string;
  /** Text before the reason, e.g. "Sync failed: ". */
  prefix?: string;
  className?: string;
  'data-testid'?: string;
  role?: string;
}) {
  const [open, setOpen] = useState(false);
  const { message, detail } = friendlyConnectionError(error, fallback);
  return (
    <div className={`${className} max-w-full break-words`} {...rest}>
      <span>
        {prefix}
        {message}
      </span>
      {detail && (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="ml-2 underline underline-offset-2 opacity-80 hover:opacity-100"
        >
          {open ? 'Hide details' : 'Details'}
        </button>
      )}
      {open && detail && (
        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-border bg-muted/40 p-2 font-mono text-[11px] text-muted-foreground">
          {detail}
        </pre>
      )}
    </div>
  );
}
