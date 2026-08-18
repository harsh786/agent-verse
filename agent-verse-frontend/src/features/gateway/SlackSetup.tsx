/**
 * SlackSetup — Slack workspace connection wizard.
 * JARVIS motion: JARVISPageShell + spring steps.
 */
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Hash, CheckCircle2, ExternalLink, Loader2, ChevronRight, AlertTriangle } from 'lucide-react';
import { JARVISPageShell, SPRING_FAST, SPRING_PANEL } from '@/components/ui/JARVISPageShell';
import { Input } from '@/components/ui/input';
import { useMutation } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

interface SlackSetupProps { orgId: string; onConnected?: () => void; }

export function SlackSetup({ orgId, onConnected }: SlackSetupProps) {
  const [step, setStep]           = useState(1);
  const [botToken, setBotToken]   = useState('');
  const [signingSecret, setSigningSecret] = useState('');

  const connect = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/org/${orgId}/gateway/config`, {
        method: 'PUT',
        body: JSON.stringify({ slack: { bot_token: botToken, signing_secret: signingSecret } }),
      }),
    onSuccess: () => { setStep(3); onConnected?.(); },
  });

  return (
    <JARVISPageShell className="max-w-lg mx-auto px-4 py-8">
      <div className="flex items-center gap-3 mb-6">
        <div className="h-10 w-10 rounded-xl bg-purple-500/10 border border-purple-500/20 flex items-center justify-center">
          <Hash className="h-5 w-5 text-purple-400" aria-hidden />
        </div>
        <div>
          <h1 className="text-base font-semibold text-[#F1F5F9]">Connect Slack Workspace</h1>
          <p className="text-[11px] text-[#475569]">Use /org commands from Slack</p>
        </div>
      </div>

      <AnimatePresence mode="wait">
        {step === 1 && (
          <motion.div key="s1" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={SPRING_FAST} className="space-y-4">
            <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4 space-y-3">
              <p className="text-sm text-[#94A3B8]">Create a Slack app at api.slack.com/apps, then:</p>
              <ol className="space-y-1.5 text-sm text-[#94A3B8] list-decimal list-inside">
                <li>Add <strong className="text-[#F1F5F9]">Bot Token Scopes</strong>: chat:write, commands, channels:read</li>
                <li>Copy the <strong className="text-[#F1F5F9]">Bot User OAuth Token</strong></li>
                <li>Copy the <strong className="text-[#F1F5F9]">Signing Secret</strong></li>
                <li>Install app to your workspace</li>
              </ol>
              <a href="https://api.slack.com/apps" target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 text-xs text-purple-400 hover:underline">
                <ExternalLink className="h-3.5 w-3.5" aria-hidden /> Open Slack API
              </a>
            </div>
            <button onClick={() => setStep(2)} style={{ touchAction: 'manipulation' }} className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-purple-600 text-white text-sm font-semibold hover:bg-purple-500 transition-colors min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/50">
              I have my credentials <ChevronRight className="h-4 w-4" aria-hidden />
            </button>
          </motion.div>
        )}

        {step === 2 && (
          <motion.div key="s2" initial={{ opacity: 0, x: 20 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -20 }} transition={SPRING_FAST} className="space-y-4">
            <div className="space-y-3">
              <div className="space-y-1.5">
                <label className="text-xs text-[#475569]" htmlFor="slack-bot-token">Bot Token</label>
                <Input id="slack-bot-token" value={botToken} onChange={e => setBotToken(e.target.value)} placeholder="xoxb-..." type="password" className="bg-[#0F1623] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569] font-mono" />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs text-[#475569]" htmlFor="slack-signing">Signing Secret</label>
                <Input id="slack-signing" value={signingSecret} onChange={e => setSigningSecret(e.target.value)} placeholder="abc123..." type="password" className="bg-[#0F1623] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569] font-mono" />
              </div>
            </div>
            <button
              onClick={() => connect.mutate()}
              disabled={connect.isPending || !botToken.trim() || !signingSecret.trim()}
              style={{ touchAction: 'manipulation' }}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-purple-600 text-white text-sm font-semibold hover:bg-purple-500 transition-colors min-h-[44px] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-400/50"
            >
              {connect.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Connect Slack'}
            </button>
            {connect.isError && (
              <div className="flex items-center gap-2 text-xs text-red-400">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> Connection failed. Check credentials.
              </div>
            )}
          </motion.div>
        )}

        {step === 3 && (
          <motion.div key="s3" initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={SPRING_PANEL} className="text-center py-8 space-y-4">
            <div className="h-16 w-16 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mx-auto">
              <CheckCircle2 className="h-8 w-8 text-emerald-400" aria-hidden />
            </div>
            <div>
              <h2 className="text-base font-semibold text-[#F1F5F9]">Slack Connected!</h2>
              <p className="text-sm text-[#475569] mt-1">Use <code className="text-purple-400">/org status</code> in any channel to get started.</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </JARVISPageShell>
  );
}

export default SlackSetup;
