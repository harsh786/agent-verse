/**
 * LoginGreetingPlayer — ambient indicator while OmniVoice greeting plays.
 *
 * Renders in OrgPage header. Shows animated waveform bars + mute button.
 * Disappears when audio ends (via AnimatePresence exit).
 *
 * D-7: Speaks real OrgService.get_org_health() data (active_missions etc.)
 * D-2: Narrates DigestGenerator WYWA digest if updates exist
 * D-5: Language auto-detected from org.jurisdiction on server side
 *
 * Accessibility:
 *   role="status" + aria-live="polite"
 *   Mute button has aria-label
 *   Waveform bars are aria-hidden
 *   prefers-reduced-motion: hook skips playback entirely
 *
 * Uses useLoginGreeting hook (spec Task 8.2):
 *   - Plays once per session (sessionStorage)
 *   - 800ms delay before play
 *   - Respects prefers-reduced-motion
 */
import { motion, AnimatePresence } from 'framer-motion';
import { VolumeX } from 'lucide-react';
import { useLoginGreeting } from '@/lib/voice/useLoginGreeting';

interface LoginGreetingPlayerProps {
  orgId:     string;
  userName?: string;
  language?: string;
  className?: string;
}

export function LoginGreetingPlayer({
  orgId, userName = 'there', language = 'en', className,
}: LoginGreetingPlayerProps) {
  const { isPlaying, stop } = useLoginGreeting({ orgId, userName, language });

  return (
    <AnimatePresence>
      {isPlaying && (
        <motion.div
          initial={{ opacity: 1, scale: 0.9, x: 10 }}
          animate={{ opacity: 1, scale: 1, x: 0 }}
          exit={{ opacity: 0, scale: 0.9, x: 10 }}
          transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-full
            bg-[#0D1526]/80 border border-[#00D4FF]/30 backdrop-blur-sm ${className ?? ''}`}
          role="status"
          aria-live="polite"
          aria-label="JARVIS speaking your daily brief"
        >
          {/* Sound bars */}
          <div className="flex items-end gap-0.5 h-4" aria-hidden="true">
            {[0.6, 1.0, 0.7, 1.2, 0.5].map((h, i) => (
              <motion.span
                key={i}
                className="w-0.5 bg-[#00D4FF] rounded-full"
                animate={{ scaleY: [0.3, h, 0.3] }}
                transition={{
                  duration: 0.65,
                  repeat: Infinity,
                  delay: i * 0.13,
                  ease: 'easeInOut',
                }}
                style={{ height: '100%', transformOrigin: 'bottom' }}
              />
            ))}
          </div>

          <span className="text-[10px] text-[#00D4FF]/80 font-mono tracking-widest select-none">
            BRIEF
          </span>

          {/* Mute button */}
          <button
            onClick={stop}
            aria-label="Mute daily brief"
            className="p-0.5 rounded-full text-[#00D4FF]/60 hover:text-[#00D4FF]
              hover:bg-[#00D4FF]/10 transition-colors"
            style={{ touchAction: 'manipulation' }}
          >
            <VolumeX className="h-3 w-3" aria-hidden />
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
