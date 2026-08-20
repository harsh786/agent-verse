/**
 * GuardrailShield — Red shield that materializes when a guardrail fires.
 * Spec §3.7: Scales in with SPRING_BOUNCY, shows rule name, dims after 5s.
 */
import { useEffect, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Shield } from 'lucide-react';

interface GuardrailShieldProps {
  ruleName?:  string;
  visible:    boolean;
  className?: string;
}

export function GuardrailShield({ ruleName, visible, className = '' }: GuardrailShieldProps) {
  const reduce  = useReducedMotion();
  const [dimmed, setDimmed] = useState(false);

  useEffect(() => {
    if (!visible) { setDimmed(false); return; }
    const t = setTimeout(() => setDimmed(true), 5000);
    return () => clearTimeout(t);
  }, [visible]);

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0 }}
          animate={{ opacity: dimmed ? 0.3 : 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.7 }}
          transition={{ type: 'spring', stiffness: 450, damping: 18 }}
          className={`flex flex-col items-center gap-1.5 ${className}`}
          role="alert"
          aria-live="assertive"
        >
          {/* Shield SVG with glow */}
          <motion.div
            animate={dimmed ? {} : { boxShadow: ['0 0 16px rgba(255,51,102,0.6)', '0 0 32px rgba(255,51,102,0.9)', '0 0 16px rgba(255,51,102,0.6)'] }}
            transition={{ duration: 1.2, repeat: dimmed ? 0 : Infinity, ease: 'easeInOut' }}
            className="rounded-full bg-[#FF3366]/10 p-2 border border-[#FF3366]/40"
          >
            <Shield className="h-6 w-6 text-[#FF3366]" aria-hidden />
          </motion.div>

          {ruleName && (
            <motion.span
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: dimmed ? 0.3 : 1, y: 0 }}
              transition={{ delay: 0.15, type: 'spring', stiffness: 380, damping: 30 }}
              className="text-[10px] text-[#FF3366] font-medium text-center max-w-[120px] leading-tight"
            >
              Blocked: {ruleName.slice(0, 40)}
            </motion.span>
          )}

          {/* Burst particles as CSS rings */}
          {!reduce && !dimmed && (
            <>
              {[1, 2, 3].map(i => (
                <motion.div
                  key={i}
                  className="absolute rounded-full border border-[#FF3366]"
                  initial={{ width: 20, height: 20, opacity: 0.8 }}
                  animate={{ width: 60 + i * 20, height: 60 + i * 20, opacity: 0 }}
                  transition={{ duration: 0.6 + i * 0.15, delay: i * 0.1, ease: 'easeOut' }}
                  style={{ top: '50%', left: '50%', transform: 'translate(-50%,-50%)' }}
                />
              ))}
            </>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
