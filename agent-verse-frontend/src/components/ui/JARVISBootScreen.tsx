/**
 * JARVISBootScreen — Full-screen Iron Man JARVIS-style startup animation.
 *
 * Sequence:
 *  0.0s  Black screen fades in
 *  0.2s  Hexagonal scan grid materialises
 *  0.5s  Central glowing ring pulses outward (3 rings)
 *  0.8s  AI core dot with rotating orbit arms appears
 *  1.2s  Boot text types out: "AGENTVERSE OS v1.0  INITIALIZING..."
 *  1.8s  Status lines scroll: SYSTEMS ONLINE, AGENTS READY, etc.
 *  2.8s  Full screen retracts upward revealing the main UI
 *
 * Usage:
 *   <JARVISBootScreen onComplete={() => setBooted(true)} orgName="Acme AI" />
 */
import { useEffect, useState } from 'react';
import { motion, AnimatePresence, useAnimation } from 'framer-motion';

interface JARVISBootScreenProps {
  orgName?: string;
  onComplete: () => void;
  /** Duration in ms before auto-completing (default 3200) */
  duration?: number;
}

const BOOT_LINES = [
  { text: 'NEURAL MESH............OK', delay: 1400 },
  { text: 'AGENT RUNTIME..........OK', delay: 1900 },
  { text: 'MEMORY VAULT...........OK', delay: 2350 },
  { text: 'GOVERNANCE ENGINE......OK', delay: 2750 },
  { text: 'MISSION PLANNER........OK', delay: 3150 },
  { text: 'ALL SYSTEMS NOMINAL', delay: 3700, highlight: true },
];

function useTypingEffect(text: string, startDelay: number, speed = 28) {
  const [displayed, setDisplayed] = useState('');
  useEffect(() => {
    let i = 0;
    const timeout = setTimeout(() => {
      const interval = setInterval(() => {
        setDisplayed(text.slice(0, ++i));
        if (i >= text.length) clearInterval(interval);
      }, speed);
      return () => clearInterval(interval);
    }, startDelay);
    return () => clearTimeout(timeout);
  }, [text, startDelay, speed]);
  return displayed;
}

/** Animated hex grid background */
function HexGrid() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.6, delay: 0.1 }}
      className="absolute inset-0 pointer-events-none overflow-hidden"
      style={{ zIndex: 1 }}
    >
      <svg width="100%" height="100%" style={{ position: 'absolute', inset: 0 }}>
        <defs>
          <pattern id="hex" x="0" y="0" width="60" height="52" patternUnits="userSpaceOnUse">
            <polygon
              points="30,2 58,17 58,47 30,62 2,47 2,17"
              fill="none"
              stroke="rgba(0,212,255,0.07)"
              strokeWidth="1"
            />
          </pattern>
          <radialGradient id="fadeEdge" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#0A0D14" stopOpacity="0" />
            <stop offset="100%" stopColor="#0A0D14" stopOpacity="1" />
          </radialGradient>
        </defs>
        <rect width="100%" height="100%" fill="url(#hex)" />
        <rect width="100%" height="100%" fill="url(#fadeEdge)" />
      </svg>
    </motion.div>
  );
}

/** Three concentric expanding ring pulses */
function RingPulses() {
  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none" style={{ zIndex: 2 }}>
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          initial={{ scale: 0.2, opacity: 0 }}
          animate={{ scale: [0.2, 1.8, 2.4], opacity: [0, 0.5, 0] }}
          transition={{
            duration: 2.8,
            repeat: Infinity,
            delay: i * 0.85,
            ease: 'easeOut',
          }}
          style={{
            position: 'absolute',
            width: 300,
            height: 300,
            borderRadius: '50%',
            border: '1px solid rgba(0,212,255,0.4)',
            boxShadow: '0 0 20px rgba(0,212,255,0.15)',
          }}
        />
      ))}
    </div>
  );
}

/** Central AI core — rotating orbit arms around a glowing dot */
function AICore() {
  return (
    <motion.div
      initial={{ scale: 0, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ type: 'spring', stiffness: 260, damping: 22, delay: 0.55 }}
      className="absolute inset-0 flex items-center justify-center pointer-events-none"
      style={{ zIndex: 3 }}
    >
      <div className="relative" style={{ width: 160, height: 160 }}>
        {/* Outer slow ring */}
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ duration: 12, repeat: Infinity, ease: 'linear' }}
          style={{
            position: 'absolute', inset: 0,
            border: '1px dashed rgba(0,212,255,0.35)',
            borderRadius: '50%',
          }}
        >
          {/* Orbit dot 1 */}
          <div style={{ position: 'absolute', top: -4, left: '50%', transform: 'translateX(-50%)', width: 8, height: 8, borderRadius: '50%', background: '#00D4FF', boxShadow: '0 0 10px #00D4FF' }} />
        </motion.div>

        {/* Middle counter-rotating ring */}
        <motion.div
          animate={{ rotate: -360 }}
          transition={{ duration: 7, repeat: Infinity, ease: 'linear' }}
          style={{
            position: 'absolute', inset: 20,
            border: '1px dashed rgba(0,212,255,0.5)',
            borderRadius: '50%',
          }}
        >
          {/* Orbit dot 2 */}
          <div style={{ position: 'absolute', bottom: -4, left: '50%', transform: 'translateX(-50%)', width: 6, height: 6, borderRadius: '50%', background: '#7C3AED', boxShadow: '0 0 8px #7C3AED' }} />
        </motion.div>

        {/* Inner fast ring */}
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ duration: 3.5, repeat: Infinity, ease: 'linear' }}
          style={{
            position: 'absolute', inset: 42,
            border: '2px solid rgba(0,212,255,0.7)',
            borderRadius: '50%',
            borderTopColor: 'transparent',
          }}
        />

        {/* Core dot */}
        <motion.div
          animate={{
            boxShadow: [
              '0 0 20px rgba(0,212,255,0.6), 0 0 60px rgba(0,212,255,0.2)',
              '0 0 40px rgba(0,212,255,0.9), 0 0 100px rgba(0,212,255,0.4)',
              '0 0 20px rgba(0,212,255,0.6), 0 0 60px rgba(0,212,255,0.2)',
            ],
          }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'easeInOut' }}
          style={{
            position: 'absolute',
            inset: 0,
            margin: 'auto',
            width: 32,
            height: 32,
            borderRadius: '50%',
            background: 'radial-gradient(circle, #00D4FF 0%, rgba(0,212,255,0.3) 70%)',
          }}
        />
      </div>
    </motion.div>
  );
}

export function JARVISBootScreen({ orgName = 'AgentVerse OS', onComplete, duration = 3200 }: JARVISBootScreenProps) {
  const [retracting, setRetracting] = useState(false);
  const [visibleLines, setVisibleLines] = useState<number[]>([]);
  const title = useTypingEffect(`INITIALIZING ${orgName.toUpperCase()}`, 900, 22);
  const controls = useAnimation();

  // Schedule each boot line
  useEffect(() => {
    const timers = BOOT_LINES.map(({ delay }, idx) =>
      setTimeout(() => setVisibleLines(prev => [...prev, idx]), delay)
    );
    return () => timers.forEach(clearTimeout);
  }, []);

  // Auto-retract after duration
  useEffect(() => {
    const t = setTimeout(async () => {
      setRetracting(true);
      await controls.start({ y: '-100%', opacity: 0, transition: { duration: 0.65, ease: [0.4, 0, 0.2, 1] } });
      onComplete();
    }, duration);
    return () => clearTimeout(t);
  }, [duration, onComplete, controls]);

  return (
    <AnimatePresence>
      {!retracting || true ? (
        <motion.div
          animate={controls}
          initial={{ y: 0, opacity: 1 }}
          className="fixed inset-0 flex flex-col items-center justify-center overflow-hidden select-none"
          style={{ background: '#060810', zIndex: 9999 }}
          role="status"
          aria-label="Initializing AgentVerse"
          aria-live="polite"
        >
          <HexGrid />
          <RingPulses />
          <AICore />

          {/* Boot text overlay */}
          <div className="relative flex flex-col items-center gap-6 text-center" style={{ zIndex: 10 }}>
            {/* Animated scan line */}
            <motion.div
              initial={{ scaleX: 0 }}
              animate={{ scaleX: 1 }}
              transition={{ duration: 0.8, delay: 0.3, ease: 'easeOut' }}
              className="w-64 h-px"
              style={{ background: 'linear-gradient(90deg, transparent, #00D4FF, transparent)' }}
            />

            {/* Main title typing effect */}
            <div className="mt-4 space-y-1">
              <motion.p
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.8 }}
                className="font-mono text-[10px] tracking-[0.4em] text-[#00D4FF]/60 uppercase"
              >
                AGENTVERSE
              </motion.p>
              <p
                className="font-mono text-sm tracking-[0.2em] text-[#00D4FF] min-h-[20px]"
                aria-label={title}
              >
                {title}
                <motion.span
                  animate={{ opacity: [1, 0, 1] }}
                  transition={{ duration: 0.7, repeat: Infinity }}
                  className="inline-block w-1 h-3 bg-[#00D4FF] ml-0.5 align-middle"
                />
              </p>
            </div>

            {/* Boot status lines */}
            <div className="space-y-1 text-left w-56">
              {BOOT_LINES.map((line, idx) => (
                <AnimatePresence key={idx}>
                  {visibleLines.includes(idx) && (
                    <motion.p
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.25, ease: 'easeOut' }}
                      className={`font-mono text-[10px] tracking-wider ${
                        line.highlight
                          ? 'text-emerald-400 font-semibold'
                          : 'text-[#475569]'
                      }`}
                    >
                      {line.highlight && (
                        <motion.span
                          animate={{ opacity: [1, 0.4, 1] }}
                          transition={{ duration: 1.2, repeat: Infinity }}
                          className="mr-2 text-emerald-400"
                        >
                          ●
                        </motion.span>
                      )}
                      {line.text}
                    </motion.p>
                  )}
                </AnimatePresence>
              ))}
            </div>

            {/* Progress bar */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 1.2 }}
              className="w-56 h-0.5 bg-[#1A1F2E] rounded-full overflow-hidden"
            >
              <motion.div
                initial={{ width: '0%' }}
                animate={{ width: '100%' }}
                transition={{ duration: 3.8, delay: 1.3, ease: 'easeInOut' }}
                className="h-full bg-gradient-to-r from-[#00D4FF] to-[#7C3AED]"
                style={{ boxShadow: '0 0 8px rgba(0,212,255,0.8)' }}
              />
            </motion.div>

            {/* Bottom scan line */}
            <motion.div
              initial={{ scaleX: 0 }}
              animate={{ scaleX: 1 }}
              transition={{ duration: 0.8, delay: 0.3, ease: 'easeOut' }}
              className="w-64 h-px"
              style={{ background: 'linear-gradient(90deg, transparent, #00D4FF, transparent)' }}
            />
          </div>

          {/* Corner decorations */}
          {['top-4 left-4', 'top-4 right-4', 'bottom-4 left-4', 'bottom-4 right-4'].map((pos, i) => (
            <motion.div
              key={pos}
              initial={{ opacity: 0, scale: 0 }}
              animate={{ opacity: 0.6, scale: 1 }}
              transition={{ delay: 0.2 + i * 0.05, duration: 0.3 }}
              className={`absolute ${pos} w-6 h-6 border-[#00D4FF]`}
              style={{
                borderTopWidth: pos.includes('top') ? 1 : 0,
                borderBottomWidth: pos.includes('bottom') ? 1 : 0,
                borderLeftWidth: pos.includes('left') ? 1 : 0,
                borderRightWidth: pos.includes('right') ? 1 : 0,
              }}
            />
          ))}
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

export default JARVISBootScreen;
