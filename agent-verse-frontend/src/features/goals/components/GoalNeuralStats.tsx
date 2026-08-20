/**
 * GoalNeuralStats — Token/guardrail/cost/HITL HUD overlay for goal execution.
 * Spec §4: Positioned above the execution graph. Live counters.
 */
import { motion } from 'framer-motion';
import { Shield, Clock, Zap, DollarSign } from 'lucide-react';
import { cn } from '@/lib/utils';

interface GoalNeuralStatsProps {
  tokenInput:    number;
  tokenOutput:   number;
  costUsd:       number;
  guardrailFired: number;
  guardrailBlocked: number;
  hitlPending:   number;
  hitlApproved:  number;
  model?:        string;
  className?:    string;
}

export function GoalNeuralStats({
  tokenInput, tokenOutput, costUsd, guardrailFired, hitlPending, hitlApproved, model, className,
}: GoalNeuralStatsProps) {
  const totalTokens = tokenInput + tokenOutput;
  if (totalTokens === 0 && guardrailFired === 0 && hitlPending === 0 && hitlApproved === 0) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: 'spring', stiffness: 280, damping: 26 }}
      className={cn('flex flex-wrap items-center gap-2', className)}
      aria-label="Goal execution statistics"
    >
      {/* Token count */}
      {totalTokens > 0 && (
        <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-[#0A0F1A] border border-[#00D4FF]/20">
          <Zap className="h-3 w-3 text-[#00D4FF]" aria-hidden />
          <span className="text-[10px] font-mono text-[#00D4FF] tabular-nums">{totalTokens.toLocaleString()} tok</span>
          {model && (
            <span className="text-[9px] text-[#5A7494] ml-1">{model.slice(0, 16)}</span>
          )}
        </div>
      )}

      {/* Cost */}
      {costUsd > 0 && (
        <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-[#0A0F1A] border border-[#34D399]/20">
          <DollarSign className="h-3 w-3 text-[#34D399]" aria-hidden />
          <span className="text-[10px] font-mono text-[#34D399] tabular-nums">${costUsd.toFixed(4)}</span>
        </div>
      )}

      {/* Guardrail */}
      {guardrailFired > 0 && (
        <motion.div
          className="flex items-center gap-1 px-2 py-1 rounded-lg bg-[#FF3366]/10 border border-[#FF3366]/30"
          animate={{ opacity: [1, 0.6, 1] }}
          transition={{ duration: 2, repeat: Infinity }}
        >
          <Shield className="h-3 w-3 text-[#FF3366]" aria-hidden />
          <span className="text-[10px] font-mono text-[#FF3366]">{guardrailFired} blocked</span>
        </motion.div>
      )}

      {/* HITL pending */}
      {hitlPending > 0 && (
        <motion.div
          className="flex items-center gap-1 px-2 py-1 rounded-lg bg-[#FFB300]/10 border border-[#FFB300]/30"
          animate={{ opacity: [1, 0.5, 1] }}
          transition={{ duration: 1.5, repeat: Infinity }}
        >
          <Clock className="h-3 w-3 text-[#FFB300]" aria-hidden />
          <span className="text-[10px] font-mono text-[#FFB300]">{hitlPending} approval{hitlPending > 1 ? 's' : ''}</span>
        </motion.div>
      )}

      {/* HITL approved */}
      {hitlApproved > 0 && (
        <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-[#00E676]/10 border border-[#00E676]/20">
          <Clock className="h-3 w-3 text-[#00E676]" aria-hidden />
          <span className="text-[10px] font-mono text-[#00E676]">{hitlApproved} approved</span>
        </div>
      )}
    </motion.div>
  );
}
