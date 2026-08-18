/**
 * TelegramSetup — Telegram bot connection wizard.
 *
 * JARVIS motion: JARVISPageShell + SPRING_PANEL step transitions.
 */
import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MessageCircle, CheckCircle2, ExternalLink, AlertTriangle, Loader2, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { JARVISPageShell, SPRING_FAST, SPRING_PANEL } from '@/components/ui/JARVISPageShell';
import { Input } from '@/components/ui/input';
import { useMutation } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api/client';

interface TelegramSetupProps {
  orgId: string;
  onConnected?: () => void;
}

const STEPS = [
  { id: 1, title: 'Create Bot',    desc: 'Open @BotFather on Telegram and create a new bot' },
  { id: 2, title: 'Enter Token',   desc: 'Paste the bot token from BotFather'               },
  { id: 3, title: 'Authorize',     desc: 'Add authorized Telegram user IDs'                 },
  { id: 4, title: 'Done',          desc: 'Your Telegram bot is ready'                       },
];

export function TelegramSetup({ orgId, onConnected }: TelegramSetupProps) {
  const [step, setStep]         = useState(1);
  const [token, setToken]       = useState('');
  const [userId, setUserId]     = useState('');
  const [users, setUsers]       = useState<string[]>([]);


  const connect = useMutation({
    mutationFn: () =>
      apiFetch(`/v1/org/${orgId}/gateway/config`, {
        
        method: 'PUT',
        body: JSON.stringify({
          telegram: { bot_token: token, authorized_user_ids: users },
        }),
      }),
    onSuccess: () => { setStep(4); onConnected?.(); },
  });


  const addUser = () => {
    if (userId.trim() && !users.includes(userId.trim())) {
      setUsers(u => [...u, userId.trim()]);
      setUserId('');
    }
  };

  return (
    <JARVISPageShell className="max-w-lg mx-auto px-4 py-8">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <div className="h-10 w-10 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
          <MessageCircle className="h-5 w-5 text-blue-400" aria-hidden />
        </div>
        <div>
          <h1 className="text-base font-semibold text-[#F1F5F9]">Connect Telegram Bot</h1>
          <p className="text-[11px] text-[#475569]">Control your AI org from Telegram</p>
        </div>
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-2 mb-6" aria-label="Setup steps">
        {STEPS.map((s, i) => (
          <div key={s.id} className="flex items-center gap-2 flex-1">
            <div className={cn(
              'h-6 w-6 rounded-full flex items-center justify-center text-[10px] font-bold transition-colors flex-shrink-0',
              step > s.id ? 'bg-emerald-500 text-white'
              : step === s.id ? 'bg-[#00D4FF] text-[#0A0D14]'
              : 'bg-[#1E2535] text-[#475569]',
            )}>
              {step > s.id ? <CheckCircle2 className="h-3.5 w-3.5" /> : s.id}
            </div>
            {i < STEPS.length - 1 && (
              <div className={cn('flex-1 h-px', step > s.id ? 'bg-emerald-500/50' : 'bg-[#1E2535]')} aria-hidden />
            )}
          </div>
        ))}
      </div>

      {/* Step content */}
      <AnimatePresence mode="wait">
        {step === 1 && (
          <motion.div
            key="step1"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={SPRING_FAST}
            className="space-y-4"
          >
            <div className="bg-[#0F1623] border border-[#1E2535] rounded-xl p-4 space-y-3">
              <p className="text-sm text-[#94A3B8]">Open Telegram and:</p>
              <ol className="space-y-2 text-sm text-[#94A3B8] list-decimal list-inside">
                <li>Search for <span className="text-[#00D4FF] font-mono">@BotFather</span></li>
                <li>Send <span className="font-mono text-[#94A3B8] bg-[#1A1F2E] px-1.5 py-0.5 rounded">/newbot</span></li>
                <li>Follow instructions to name your bot</li>
                <li>Copy the bot token BotFather gives you</li>
              </ol>
              <a
                href="https://t.me/BotFather"
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-xs text-[#00D4FF] hover:underline"
              >
                <ExternalLink className="h-3.5 w-3.5" aria-hidden /> Open @BotFather
              </a>
            </div>
            <button
              onClick={() => setStep(2)}
              style={{ touchAction: 'manipulation' }}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-[#00D4FF] text-[#0A0D14] text-sm font-semibold hover:bg-[#00D4FF]/90 transition-colors min-h-[44px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
            >
              I have my bot token <ChevronRight className="h-4 w-4" aria-hidden />
            </button>
          </motion.div>
        )}

        {step === 2 && (
          <motion.div
            key="step2"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={SPRING_FAST}
            className="space-y-4"
          >
            <div className="space-y-2">
              <label className="text-xs text-[#475569]" htmlFor="bot-token">Bot Token</label>
              <Input
                id="bot-token"
                value={token}
                onChange={e => setToken(e.target.value)}
                placeholder="1234567890:ABCdef..."
                type="password"
                className="bg-[#0F1623] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569] font-mono"
              />
              <p className="text-[11px] text-[#475569]">Stored encrypted in your org vault. Never exposed.</p>
            </div>
            <button
              onClick={() => token.trim() && setStep(3)}
              disabled={!token.trim()}
              style={{ touchAction: 'manipulation' }}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-[#00D4FF] text-[#0A0D14] text-sm font-semibold hover:bg-[#00D4FF]/90 transition-colors min-h-[44px] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
            >
              Continue <ChevronRight className="h-4 w-4" aria-hidden />
            </button>
          </motion.div>
        )}

        {step === 3 && (
          <motion.div
            key="step3"
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={SPRING_FAST}
            className="space-y-4"
          >
            <p className="text-sm text-[#94A3B8]">
              Add Telegram user IDs that are allowed to send commands to this org.
              Find your ID via <span className="text-[#00D4FF] font-mono">@userinfobot</span>.
            </p>
            <div className="flex gap-2">
              <Input
                value={userId}
                onChange={e => setUserId(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addUser()}
                placeholder="123456789"
                className="bg-[#0F1623] border-[#1E2535] text-[#F1F5F9] placeholder:text-[#475569] font-mono"
                aria-label="Telegram user ID"
              />
              <button
                onClick={addUser}
                style={{ touchAction: 'manipulation' }}
                className="px-3 py-2 rounded-lg bg-[#1A1F2E] border border-[#1E2535] text-[#94A3B8] hover:text-[#F1F5F9] hover:border-[#00D4FF]/30 transition-colors min-h-[44px] text-sm"
              >
                Add
              </button>
            </div>
            {users.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {users.map(u => (
                  <span key={u} className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-[#1A1F2E] border border-[#1E2535] text-xs text-[#94A3B8] font-mono">
                    {u}
                    <button onClick={() => setUsers(us => us.filter(x => x !== u))} aria-label={`Remove ${u}`} className="text-[#475569] hover:text-red-400">×</button>
                  </span>
                ))}
              </div>
            )}
            <button
              onClick={() => connect.mutate()}
              disabled={connect.isPending || users.length === 0}
              style={{ touchAction: 'manipulation' }}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-[#00D4FF] text-[#0A0D14] text-sm font-semibold hover:bg-[#00D4FF]/90 transition-colors min-h-[44px] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#00D4FF]/50"
            >
              {connect.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Connect Telegram Bot'}
            </button>
            {connect.isError && (
              <div className="flex items-center gap-2 text-xs text-red-400">
                <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> Failed to connect. Check token and try again.
              </div>
            )}
          </motion.div>
        )}

        {step === 4 && (
          <motion.div
            key="step4"
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={SPRING_PANEL}
            className="text-center py-8 space-y-4"
          >
            <div className="h-16 w-16 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mx-auto">
              <CheckCircle2 className="h-8 w-8 text-emerald-400" aria-hidden />
            </div>
            <div>
              <h2 className="text-base font-semibold text-[#F1F5F9]">Telegram Connected!</h2>
              <p className="text-sm text-[#475569] mt-1">
                Your bot is ready. Send any message to start controlling your org from Telegram.
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </JARVISPageShell>
  );
}

export default TelegramSetup;
