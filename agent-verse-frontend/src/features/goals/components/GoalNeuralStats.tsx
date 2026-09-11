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
    <div
      className={cn('jarvis-rise-in flex flex-wrap items-center gap-2', className)}
      aria-label="Goal execution statistics"
    >
      {/* Token count */}
      {totalTokens > 0 && (
        <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-muted border border-primary/20">
          <Zap className="h-3 w-3 text-primary" aria-hidden />
          <span className="text-[10px] font-mono text-primary tabular-nums">{totalTokens.toLocaleString()} tok</span>
          {model && (
            <span className="text-[9px] text-muted-foreground ml-1">{model.slice(0, 16)}</span>
          )}
        </div>
      )}

      {/* Cost */}
      {costUsd > 0 && (
        <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-muted border border-emerald-500/20">
          <DollarSign className="h-3 w-3 text-emerald-500" aria-hidden />
          <span className="text-[10px] font-mono text-emerald-600 dark:text-emerald-400 tabular-nums">${costUsd.toFixed(4)}</span>
        </div>
      )}

      {/* Guardrail */}
      {guardrailFired > 0 && (
        <motion.div
          className="flex items-center gap-1 px-2 py-1 rounded-lg bg-red-500/10 border border-red-500/30"
          animate={{ opacity: [1, 0.6, 1] }}
          transition={{ duration: 2, repeat: Infinity }}
        >
          <Shield className="h-3 w-3 text-red-500" aria-hidden />
          <span className="text-[10px] font-mono text-red-600 dark:text-red-400">{guardrailFired} blocked</span>
        </motion.div>
      )}

      {/* HITL pending */}
      {hitlPending > 0 && (
        <motion.div
          className="flex items-center gap-1 px-2 py-1 rounded-lg bg-amber-500/10 border border-amber-500/30"
          animate={{ opacity: [1, 0.5, 1] }}
          transition={{ duration: 1.5, repeat: Infinity }}
        >
          <Clock className="h-3 w-3 text-amber-500" aria-hidden />
          <span className="text-[10px] font-mono text-amber-600 dark:text-amber-400">{hitlPending} approval{hitlPending > 1 ? 's' : ''}</span>
        </motion.div>
      )}

      {/* HITL approved */}
      {hitlApproved > 0 && (
        <div className="flex items-center gap-1 px-2 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
          <Clock className="h-3 w-3 text-emerald-500" aria-hidden />
          <span className="text-[10px] font-mono text-emerald-600 dark:text-emerald-400">{hitlApproved} approved</span>
        </div>
      )}
    </div>
  );
}
