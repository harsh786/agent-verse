/**
 * OAuthPopupButton — connects a REGISTERED OAuth connector via the backend's
 * real PKCE flow, in a popup window.
 *
 * Flow:
 *  1. User clicks → GET /connectors/oauth/start?server_id=… → { auth_url, state, redirect_uri }
 *  2. Popup opens to auth_url (provider's consent page, with PKCE challenge)
 *  3. Provider redirects to this app's /connectors/oauth/callback?code=…&state=…
 *  4. OAuthCallbackPage posts { type: 'oauth_callback', code, state } to the opener
 *  5. This component validates state, then GET /connectors/oauth/callback?server_id&code&state
 *     — the backend exchanges the code with the stored PKCE verifier and stores tokens
 *  6. Only a `status: "connected"` answer is reported as connected
 *
 * The old popup flow (POST /connectors/oauth/start → POST /connectors/oauth/callback)
 * never exchanged the code; the backend now answers that callback with 501
 * `oauth-token-exchange-unavailable`, so it is not used here. Without a
 * `serverId` (connector not registered yet) the button explains what to do
 * instead of starting a flow that cannot finish.
 */
import { useState, useEffect, useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Loader2, CheckCircle, Shield, AlertCircle } from 'lucide-react';
import { connectorsApi, ApiError, type PkceOAuthStart } from '@/lib/api/client';
import { toast } from '@/stores/toast';

interface OAuthPopupButtonProps {
  connectorName: string;
  displayName?: string;
  /** The tenant's registered connector (auth_type pkce/oauth_ac). Required to connect. */
  serverId?: string;
  onSuccess?: (serverId: string) => void;
}

const CALLBACK_PATH = '/connectors/oauth/callback';

const EXCHANGE_ERROR_MESSAGES: Record<string, string> = {
  oauth_invalid_state:
    'The authorization expired or was already used — start the connection again.',
  oauth_provider_rejected:
    'The OAuth provider rejected the authorization code. Check the client id/secret and redirect URI, then retry.',
  oauth_provider_unreachable:
    "The OAuth provider's token endpoint could not be reached. Retry in a moment.",
  oauth_provider_bad_response: 'The OAuth provider returned no usable token. Retry the connection.',
  oauth_token_url_rejected: "The connector's token URL is not an allowed public URL.",
  oauth_exchange_failed: 'The OAuth token exchange failed. Retry the connection.',
};

function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const body = e.body as { detail?: { code?: string; type?: string } } | undefined;
    const code = body?.detail?.code ?? body?.detail?.type;
    if (e.status === 501 && code === 'oauth-token-exchange-unavailable') {
      return 'The server could not exchange the authorization code, so the connector was NOT connected.';
    }
    // Code-exchange failures carry a stable code (OAUTH-05).
    const mapped = code ? EXCHANGE_ERROR_MESSAGES[code] : undefined;
    if (mapped) return mapped;
    return e.message || `Request failed (${e.status})`;
  }
  return e instanceof Error ? e.message : String(e);
}

/**
 * The provider must send the user back to THIS app's callback page: that page
 * relays the code to this window, which completes the exchange with the
 * tenant's credentials. A redirect_uri pointing anywhere else would land the
 * popup on a page that cannot finish the flow.
 */
function redirectProblem(start: PkceOAuthStart): string | null {
  if (!/^https?:\/\//i.test(start.auth_url)) {
    return start.auth_url || 'The connector has no OAuth authorize URL configured.';
  }
  if (!start.redirect_uri) return null;
  try {
    const u = new URL(start.redirect_uri);
    if (u.origin === window.location.origin && u.pathname === CALLBACK_PATH) return null;
  } catch {
    /* fall through */
  }
  return (
    `This connector's OAuth redirect URI (${start.redirect_uri}) does not point to ` +
    `${window.location.origin}${CALLBACK_PATH}, so the authorization cannot be completed ` +
    `here. Set auth_config.redirect_uri to that URL (and register it with the provider).`
  );
}

export function OAuthPopupButton({
  connectorName,
  displayName,
  serverId,
  onSuccess,
}: OAuthPopupButtonProps) {
  const qc = useQueryClient();
  const label = displayName ?? connectorName;
  const [popupWindow, setPopupWindow] = useState<Window | null>(null);
  const [oauthState, setOauthState] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'pending' | 'waiting' | 'success' | 'error'>(
    'idle',
  );
  const [errorMessage, setErrorMessage] = useState('');

  const fail = useCallback((message: string) => {
    setErrorMessage(message);
    setStatus('error');
    toast({ kind: 'error', message: `${label}: ${message}` });
  }, [label]);

  const completeOAuth = useMutation({
    mutationFn: (params: { code: string; state: string }) =>
      connectorsApi.completePkceOAuth(serverId ?? '', params.code, params.state),
    onSuccess: (data) => {
      if (data?.status !== 'connected') {
        fail(data?.message || `OAuth did not complete (status: ${data?.status ?? 'unknown'}).`);
        return;
      }
      setStatus('success');
      setErrorMessage('');
      toast({ kind: 'success', message: `${label} connected successfully!` });
      qc.invalidateQueries({ queryKey: ['connectors'] });
      onSuccess?.(data.server_id || serverId || '');
    },
    onError: (e) => fail(`OAuth failed: ${errorText(e)}`),
  });

  const startOAuth = useMutation({
    mutationFn: () => connectorsApi.startPkceOAuth(serverId ?? ''),
    onSuccess: (data) => {
      const problem = redirectProblem(data);
      if (problem) {
        fail(problem);
        return;
      }
      setOauthState(data.state);

      // Open OAuth popup centered on the current window
      const width = 600;
      const height = 700;
      const left = window.screenX + (window.outerWidth - width) / 2;
      const top = window.screenY + (window.outerHeight - height) / 2;

      const popup = window.open(
        data.auth_url,
        `oauth_${connectorName}`,
        `width=${width},height=${height},left=${left},top=${top},scrollbars=yes,resizable=yes`,
      );

      if (!popup) {
        fail('Popup blocked. Please allow popups for this site and try again.');
        return;
      }

      setStatus('waiting');
      setPopupWindow(popup);
    },
    onError: (e) => fail(`Failed to start OAuth: ${errorText(e)}`),
  });

  // Listen for the OAuth callback postMessage from the popup
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      // Only accept messages from the same origin
      if (event.origin !== window.location.origin) return;

      const { type, code, state, error } = (event.data ?? {}) as {
        type?: string;
        code?: string;
        state?: string;
        error?: string;
      };

      if (type !== 'oauth_callback' || !oauthState) return;

      // Close and clear the popup reference
      popupWindow?.close();
      setPopupWindow(null);

      if (error) {
        fail(`OAuth error: ${error}`);
        return;
      }

      if (code && state && state === oauthState) {
        setOauthState(null);
        completeOAuth.mutate({ code, state });
      } else {
        fail('OAuth state mismatch — please try again.');
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [popupWindow, oauthState, completeOAuth, fail]);

  // Detect when the user closes the popup without completing authorization
  useEffect(() => {
    if (!popupWindow || status !== 'waiting') return;

    const id = setInterval(() => {
      if (popupWindow.closed) {
        clearInterval(id);
        setPopupWindow(null);
        // Only reset to idle if completeOAuth hasn't already started
        setStatus((prev) => (prev === 'waiting' ? 'idle' : prev));
      }
    }, 500);

    return () => clearInterval(id);
  }, [popupWindow, status]);

  const handleClick = useCallback(() => {
    setErrorMessage('');
    setStatus('pending');
    startOAuth.mutate();
  }, [startOAuth]);

  if (!serverId) {
    return (
      <div className="space-y-1" data-testid="oauth-needs-registration">
        <button
          type="button"
          disabled
          className="flex w-full items-center justify-center gap-2 px-3 py-2 text-sm bg-muted text-muted-foreground rounded-lg cursor-not-allowed"
        >
          <Shield className="h-4 w-4" />
          Connect with {label}
        </button>
        <p className="text-[11px] text-muted-foreground">
          Configure this connector first (OAuth authorize URL, token URL and client ID), then
          connect with OAuth.
        </p>
      </div>
    );
  }

  if (status === 'success') {
    return (
      <button
        type="button"
        disabled
        className="flex items-center gap-2 px-3 py-2 text-sm bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 rounded-lg cursor-default"
      >
        <CheckCircle className="h-4 w-4" />
        Connected
      </button>
    );
  }

  const isLoading =
    status === 'pending' || status === 'waiting' || completeOAuth.isPending;

  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={handleClick}
        disabled={isLoading}
        className="flex items-center gap-2 px-3 py-2 text-sm bg-primary text-primary-foreground rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        {isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Shield className="h-4 w-4" />
        )}
        {status === 'waiting'
          ? 'Waiting for authorization…'
          : completeOAuth.isPending
          ? 'Finishing connection…'
          : `Connect with ${label}`}
        {(status === 'idle' || status === 'error') && <ExternalLink className="h-3 w-3 opacity-60" />}
      </button>
      {status === 'error' && errorMessage && (
        <p role="alert" className="flex items-start gap-1 text-[11px] text-destructive">
          <AlertCircle className="h-3 w-3 mt-0.5 flex-shrink-0" aria-hidden="true" />
          <span>Not connected — {errorMessage}</span>
        </p>
      )}
    </div>
  );
}
