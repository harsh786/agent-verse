/**
 * HITLGateNode — Animated amber approval gate node.
 * Spec §3.8: Spinning diamond, countdown, approver name. Opens on approval.
 */
import { useEffect, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Clock, CheckCircle2, XCircle } from 'lucide-react';

interface HITLGateNodeProps {
  action?:     string;
  approver?:   string;
  timeoutMs?:  number;
  status:      'waiting' | 'approved' | 'rejected';
  className?:  string;
}

export function HITLGateNode({
  action, approver, timeoutMs = 300_000, status, className = '',
}: HITLGateNodeProps) {
  const reduce   = useReducedMotion();
  const [remaining, setRemaining] = useState(timeoutMs);

  useEffect(() => {
    if (status !== 'waiting') return;
    const interval = setInterval(() => setRemaining(r => Math.max(0, r - 1000)), 1000);
    return () => clearInterval(interval);
  }, [status]);

  const mins = Math.floor(remaining / 60_000);
  const secs = Math.floor((remaining % 60_000) / 1000);
  const urgent = remaining < 60_000;

  const glow = status === 'approved'
    ? '0 0 20px rgba(0,230,118,0.40)'
    : status === 'rejected'
    ? '0 0 20px rgba(255,51,102,0.40)'
    : urgent
    ? '0 0 20px rgba(255,51,102,0.40)'
    : '0 0 16px rgba(255,179,0,0.40)';

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.7 }}
      animate={{ opacity: 1, scale: 1, boxShadow: glow }}
      exit={{ opacity: 0, scale: 0.8 }}
      transition={{ type: 'spring', stiffness: 450, damping: 18 }}
      className={`relative rounded-xl bg-[#0A0F1A] border px-4 py-3 ${
        status === 'approved' ? 'border-[#00E676]/40' :
        status === 'rejected' ? 'border-[#FF3366]/40' :
        urgent ? 'border-[#FF3366]/60' : 'border-[#FFB300]/40'
      } ${className}`}
      role="status"
      aria-label={`Approval gate: ${status}`}
    >
      {/* Spinning diamond (waiting state) */}
      <AnimatePresence>
        {status === 'waiting' && (
          <motion.div
            className="absolute -top-4 left-1/2 -translate-x-1/2"
            animate={reduce ? {} : { rotate: 360 }}
            transition={{ duration: 3, repeat: Infinity, ease: 'linear' }}
          >
            <div className={`w-6 h-6 rotate-45 border-2 ${urgent ? 'border-[#FF3366] bg-[#FF3366]/10' : 'border-[#FFB300] bg-[#FFB300]/10'}`} />
          </motion.div>
        )}
        {status === 'approved' && (
          <motion.div className="absolute -top-4 left-1/2 -translate-x-1/2"
            initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: 'spring', stiffness: 600 }}>
            <CheckCircle2 className="h-6 w-6 text-[#00E676]" aria-hidden />
          </motion.div>
        )}
        {status === 'rejected' && (
          <motion.div className="absolute -top-4 left-1/2 -translate-x-1/2"
            initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ type: 'spring', stiffness: 600 }}>
            <XCircle className="h-6 w-6 text-[#FF3366]" aria-hidden />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="flex items-center gap-2 mb-1.5">
        <Clock className={`h-3.5 w-3.5 flex-shrink-0 ${urgent ? 'text-[#FF3366]' : 'text-[#FFB300]'}`} aria-hidden />
        <span className="text-[11px] font-semibold text-[#F0F6FF]">
          {status === 'waiting' ? 'Awaiting approval' : status === 'approved' ? 'Approved ✓' : 'Rejected ✗'}
        </span>
      </div>

      {action && (
        <p className="text-[10px] text-[#A0B4CC] mb-1 font-mono truncate">{action}</p>
      )}

      {approver && (
        <p className="text-[10px] text-[#5A7494]">Approver: {approver}</p>
      )}

      {status === 'waiting' && (
        <div className="flex items-center gap-1 mt-1.5">
          <span className={`text-[10px] font-mono font-bold tabular-nums ${urgent ? 'text-[#FF3366]' : 'text-[#FFB300]'}`}>
            {mins}:{secs.toString().padStart(2, '0')}
          </span>
          <span className="text-[9px] text-[#5A7494]">remaining</span>
        </div>
      )}

      {/* Amber pulse ring when waiting */}
      {status === 'waiting' && !reduce && (
        <motion.div
          className="absolute inset-0 rounded-xl border border-[#FFB300]"
          animate={{ opacity: [0.5, 0.1, 0.5] }}
          transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
    </motion.div>
  );
}
