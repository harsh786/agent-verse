/**
 * OAuthCallbackPage — handles OAuth provider redirects and forwards the result to
 * the parent window that opened this popup via postMessage.
 *
 * Route: /connectors/oauth/callback  (public — no auth required)
 *
 * The page reads the `code`, `state`, and optional `error` query parameters sent
 * by the OAuth provider, posts a `{ type: 'oauth_callback', ... }` message to
 * `window.opener`, and auto-closes after a short delay.
 */
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CheckCircle, XCircle, Loader2 } from 'lucide-react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

export default function OAuthCallbackPage() {
  const [searchParams] = useSearchParams();
  const [status, setStatus] = useState<'processing' | 'done' | 'error'>('processing');
  const [message, setMessage] = useState('');

  useEffect(() => {
    const code = searchParams.get('code');
    const state = searchParams.get('state');
    const error = searchParams.get('error');
    const errorDesc = searchParams.get('error_description');

    if (window.opener) {
      if (error) {
        window.opener.postMessage(
          { type: 'oauth_callback', error: errorDesc ?? error },
          window.location.origin,
        );
        setStatus('error');
        setMessage(errorDesc ?? error ?? 'Authorization denied');
      } else if (code && state) {
        window.opener.postMessage(
          { type: 'oauth_callback', code, state },
          window.location.origin,
        );
        setStatus('done');
        setMessage('Authorization successful! You can close this window.');
      } else {
        window.opener.postMessage(
          { type: 'oauth_callback', error: 'Missing code or state parameter' },
          window.location.origin,
        );
        setStatus('error');
        setMessage('Invalid OAuth response. Please try again.');
      }

      // Auto-close the popup after 2 s
      const timer = setTimeout(() => window.close(), 2000);
      return () => clearTimeout(timer);
    } else {
      // Opened directly (not as a popup) — just show the received params
      setStatus('done');
      setMessage(
        'OAuth callback received. If this page does not close automatically, you may close it manually.',
      );
    }
  }, [searchParams]);

  return (
    <JARVISPageShell>
    <JARVISStagger className="min-h-screen bg-background flex items-center justify-center p-8">
      <div className="text-center max-w-sm space-y-4">
        {status === 'processing' && (
          <>
            <Loader2 className="h-12 w-12 animate-spin text-primary mx-auto" />
            <h2 className="text-lg font-semibold">Processing authorization…</h2>
          </>
        )}

        {status === 'done' && (
          <>
            <CheckCircle className="h-12 w-12 text-green-500 mx-auto" />
            <h2 className="text-lg font-semibold text-green-700 dark:text-green-400">
              Connected!
            </h2>
            <p className="text-sm text-muted-foreground">{message}</p>
          </>
        )}

        {status === 'error' && (
          <>
            <XCircle className="h-12 w-12 text-red-500 mx-auto" />
            <h2 className="text-lg font-semibold text-red-700 dark:text-red-400">
              Authorization Failed
            </h2>
            <p className="text-sm text-muted-foreground">{message}</p>
          </>
        )}
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
