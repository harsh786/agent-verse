import { useEffect, useState, type JSX } from 'react';
import { chatKnowledgeApi, type ChatKnowledgeOptInState } from '@/lib/api/client';
import { removalSummary } from '@/features/settings/chatTranscriptRemoval';

/**
 * Owner decision 7 — a person's own opt-in to index their chats as knowledge.
 * Off by default and revocable. Only the chats they created are indexed, under
 * their id, with PII and secrets redacted; revoking removes what was indexed
 * (documents under legal hold are kept and reported).
 */
export function ChatKnowledgeOptIn(): JSX.Element {
  const [state, setState] = useState<ChatKnowledgeOptInState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    chatKnowledgeApi
      .getOptIn()
      .then((s) => live && setState(s))
      .catch((e: unknown) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, []);

  const optedIn = state?.opted_in === true;
  const tenantEnabled = state?.tenant_enabled === true;
  // Revoking is always possible; opting in needs the workspace switch.
  const canToggle = state !== null && !saving && (optedIn || tenantEnabled);

  const toggle = async () => {
    setSaving(true);
    setError(null);
    setSummary(null);
    try {
      const next = await chatKnowledgeApi.setOptIn(!optedIn);
      setState((prev) => ({ ...(prev ?? next), ...next }));
      setSummary(removalSummary(next));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex items-start justify-between gap-4 mb-6 rounded-xl border border-border p-4">
      <div>
        <h2 className="text-sm font-semibold text-foreground">Use my chats as knowledge</h2>
        <p className="text-xs text-muted-foreground mt-1">
          Indexes the chats you created into your workspace&apos;s knowledge, under your name,
          with personal data and secrets redacted. Never anyone else&apos;s chats. Turning it off
          removes your chats from the knowledge base.
        </p>
        {state !== null && !tenantEnabled && !optedIn && (
          <p className="text-xs text-muted-foreground mt-1">
            Not enabled for this workspace yet: an admin turns it on in Settings.
          </p>
        )}
        {summary && (
          <p aria-live="polite" className="text-xs text-muted-foreground mt-1">
            {summary}
          </p>
        )}
        {error && (
          <p role="alert" className="text-xs text-red-600 dark:text-red-400 mt-1">
            {error}
          </p>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={optedIn}
        aria-label="Use my chats as knowledge"
        disabled={!canToggle}
        onClick={toggle}
        className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-50 ${
          optedIn ? 'bg-primary' : 'bg-muted'
        }`}
      >
        <span
          className={`inline-block h-5 w-5 rounded-full bg-background shadow transform transition-transform mt-0.5 ${
            optedIn ? 'translate-x-5' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );
}
