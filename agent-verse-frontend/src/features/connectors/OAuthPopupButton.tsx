/**
 * OAuthPopupButton — opens an OAuth flow in a popup window and handles the callback.
 * Used for connectors that support OAuth (GitHub, Slack, Google Workspace, Jira, etc.)
 *
 * Flow:
 *  1. User clicks → POST /connectors/oauth/start → receives { auth_url, state }
 *  2. Popup opens to auth_url (provider's OAuth page)
 *  3. Provider redirects to /connectors/oauth/callback?code=…&state=…
 *  4. OAuthCallbackPage posts { type: 'oauth_callback', code, state } to opener
 *  5. This component receives the message, validates state, calls completeOAuth
 *  6. On success: invalidates queries and calls onSuccess callback
 */
import { useState, useEffect, useCallback } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink, Loader2, CheckCircle, Shield } from 'lucide-react';
import { connectorsApi } from '@/lib/api/client';
import { toast } from '@/stores/toast';

interface OAuthPopupButtonProps {
  connectorName: string;
  displayName?: string;
  onSuccess?: (serverId: string) => void;
}

export function OAuthPopupButton({
  connectorName,
  displayName,
  onSuccess,
}: OAuthPopupButtonProps) {
  const qc = useQueryClient();
  const [popupWindow, setPopupWindow] = useState<Window | null>(null);
  const [oauthState, setOauthState] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'pending' | 'waiting' | 'success' | 'error'>(
    'idle',
  );

  const completeOAuth = useMutation({
    mutationFn: (params: { code: string; state: string }) =>
      connectorsApi.completeOAuth(params.code, params.state, connectorName),
    onSuccess: (data) => {
      setStatus('success');
      toast({
        kind: 'success',
        message: `${displayName ?? connectorName} connected successfully!`,
      });
      qc.invalidateQueries({ queryKey: ['connectors'] });
      onSuccess?.(data.server_id);
    },
    onError: (e) => {
      toast({ kind: 'error', message: `OAuth failed: ${String(e)}` });
      setStatus('error');
    },
  });

  const startOAuth = useMutation({
    mutationFn: () => connectorsApi.startOAuth(connectorName),
    onSuccess: (data) => {
      setOauthState(data.state);
      setStatus('waiting');

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
        toast({
          kind: 'error',
          message: 'Popup blocked. Please allow popups for this site and try again.',
        });
        setStatus('error');
        return;
      }

      setPopupWindow(popup);
    },
    onError: (e) => {
      toast({ kind: 'error', message: `Failed to start OAuth: ${String(e)}` });
      setStatus('error');
    },
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

      if (type !== 'oauth_callback') return;

      // Close and clear the popup reference
      popupWindow?.close();
      setPopupWindow(null);

      if (error) {
        toast({ kind: 'error', message: `OAuth error: ${error}` });
        setStatus('error');
        return;
      }

      if (code && state && state === oauthState) {
        completeOAuth.mutate({ code, state });
      } else {
        toast({ kind: 'error', message: 'OAuth state mismatch — please try again.' });
        setStatus('error');
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [popupWindow, oauthState, completeOAuth]);

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
    setStatus('pending');
    startOAuth.mutate();
  }, [startOAuth]);

  if (status === 'success') {
    return (
      <button
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
    <button
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
        : `Connect with ${displayName ?? connectorName}`}
      {status === 'idle' && <ExternalLink className="h-3 w-3 opacity-60" />}
    </button>
  );
}
