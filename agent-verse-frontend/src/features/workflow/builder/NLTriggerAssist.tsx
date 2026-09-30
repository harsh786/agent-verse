/**
 * NLTriggerAssist — describe when a workflow should run in plain words and
 * apply the parsed trigger (POST /workflows/nl-trigger-preview).
 *
 * Simple phrases are parsed instantly; anything else goes to the AI parser
 * (charged to the tenant). A 422 carries the server's reason plus phrasings
 * that work, which are shown instead of a generic error.
 */
import { useState } from 'react';
import { Sparkles, Loader2 } from 'lucide-react';
import { workflowEngineApi } from '../../../lib/api/client';

const EXAMPLE_TRIGGER_PHRASES = [
  'every day at midnight',
  'every Monday',
  'hourly',
  'when a webhook is called',
];

interface Props {
  onApply: (update: Record<string, unknown>) => void;
}

export function NLTriggerAssist({ onApply }: Props) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState<string | null>(null);

  const suggest = async () => {
    const description = text.trim();
    if (!description) return;
    setBusy(true);
    setError(null);
    setApplied(null);
    try {
      const { trigger } = await workflowEngineApi.nlTriggerPreview(description);
      const type = String(trigger.type ?? 'api');
      const schedule = (trigger.schedule ?? {}) as { cron?: string };
      if (type === 'schedule' && schedule.cron) {
        onApply({ triggerType: 'schedule', cron: schedule.cron });
        setApplied(`Schedule · ${schedule.cron}`);
      } else if (type === 'webhook' || type === 'api') {
        onApply({ triggerType: type });
        setApplied(type === 'webhook' ? 'Webhook' : 'Manual / API');
      } else {
        setError(`That describes a “${type}” trigger, which can't start runs yet.`);
      }
    } catch (err: unknown) {
      const message = err instanceof Error && err.message ? err.message : '';
      setError(message || 'Could not understand that description.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <label htmlFor="nl-trigger-input" className="block text-xs font-medium text-white/50">
        Or describe it
      </label>
      <div className="flex gap-2">
        <input
          id="nl-trigger-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              void suggest();
            }
          }}
          placeholder="e.g. every weekday at 9am"
          className="flex-1 px-3 py-2 rounded-xl bg-[#0F1826]/5 border border-white/10 text-white/90
                     text-xs focus:outline-none focus:ring-2 focus:ring-sky-500"
        />
        <button
          type="button"
          onClick={() => void suggest()}
          disabled={busy || !text.trim()}
          className="flex items-center gap-1 px-3 py-2 rounded-xl bg-sky-600/60 hover:bg-sky-600/80
                     text-xs text-white disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
          Apply
        </button>
      </div>
      {applied && <p className="text-xs text-emerald-400">Applied: {applied}</p>}
      {error && (
        <div className="text-xs text-amber-400 space-y-1" role="alert">
          <p>{error}</p>
          <p className="text-white/40">
            Phrases that always work: {EXAMPLE_TRIGGER_PHRASES.map((p) => `“${p}”`).join(', ')}
          </p>
        </div>
      )}
    </div>
  );
}
