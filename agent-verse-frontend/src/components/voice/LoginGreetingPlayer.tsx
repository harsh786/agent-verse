/**
 * LoginGreetingPlayer — plays the OmniVoice spoken greeting on org page load.
 *
 * Renders as a small ambient indicator: glowing dot + "JARVIS speaking..." label.
 * Fades out after greeting completes. No user interaction required.
 *
 * D-7: Speaks real org health data (active missions, pending approvals).
 * D-2: Narrates "While You Were Away" digest if there are updates.
 * D-5: Language auto-detected from org jurisdiction on server side.
 */
import { motion, AnimatePresence } from 'framer-motion';
import { Volume2 } from 'lucide-react';
import { useGreeting } from '@/lib/voice/useGreeting';

interface LoginGreetingPlayerProps {
  orgId: string;
  userName?: string;
  className?: string;
}

export function LoginGreetingPlayer({ orgId, userName, className }: LoginGreetingPlayerProps) {
  const { isPlaying } = useGreeting(orgId, userName);

  return (
    <AnimatePresence>
      {isPlaying && (
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.9 }}
          transition={{ type: 'spring', stiffness: 400, damping: 30 }}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-full bg-[#0D1526]/80 border border-[#00D4FF]/30 backdrop-blur-sm ${className ?? ''}`}
          role="status"
          aria-live="polite"
          aria-label="Voice greeting playing"
        >
          {/* Pulsing speaker icon */}
          <div className="relative">
            <motion.div
              className="absolute inset-0 rounded-full bg-[#00D4FF]/20"
              animate={{ scale: [1, 1.8, 1], opacity: [0.6, 0, 0.6] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            />
            <Volume2 className="h-3.5 w-3.5 text-[#00D4FF] relative z-10" aria-hidden />
          </div>

          {/* Sound bars animation */}
          <div className="flex items-end gap-0.5 h-3.5" aria-hidden>
            {[0.6, 1, 0.8, 1.1, 0.7].map((h, i) => (
              <motion.span
                key={i}
                className="w-0.5 bg-[#00D4FF] rounded-full"
                animate={{ scaleY: [0.3, h, 0.3] }}
                transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.12 }}
                style={{ height: '100%', transformOrigin: 'bottom' }}
              />
            ))}
          </div>

          <span className="text-[10px] text-[#00D4FF]/80 font-mono tracking-wider">
            JARVIS SPEAKING
          </span>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
