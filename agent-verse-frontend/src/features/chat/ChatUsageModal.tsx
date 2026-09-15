/**
 * ChatUsageModal — per-session usage summary (tokens, cost, LLM calls, goals).
 */

import { type JSX } from 'react';
import { X, Zap, DollarSign, MessageSquare } from 'lucide-react';
import type { ChatUsageSummary } from './types/chat.types';

interface Props {
  summary: ChatUsageSummary | null;
  onClose: () => void;
}

export function ChatUsageModal({ summary, onClose }: Props): JSX.Element | null {
  if (!summary) return null;

  const stats = [
    {
      icon: <Zap className="w-4 h-4 text-indigo-500" />,
      label: 'Total tokens',
      value: summary.total_tokens.toLocaleString(),
    },
    {
      icon: <DollarSign className="w-4 h-4 text-green-500" />,
      label: 'Total cost',
      value: `$${summary.total_cost_usd.toFixed(4)}`,
    },
    {
      icon: <MessageSquare className="w-4 h-4 text-blue-500" />,
      label: 'LLM calls',
      value: summary.llm_calls.toString(),
    },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-label="Session usage"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-card rounded-2xl shadow-2xl w-96 p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-foreground">Session Usage</h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-muted"
            aria-label="Close"
          >
            <X className="w-4 h-4 text-muted-foreground" />
          </button>
        </div>

        <div className="grid grid-cols-1 gap-3">
          {stats.map((s, i) => (
            <div key={i} className="flex items-center gap-3 p-3 bg-background rounded-xl">
              {s.icon}
              <div className="flex-1">
                <p className="text-xs text-muted-foreground">{s.label}</p>
                <p className="text-sm font-semibold text-foreground">{s.value}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="mt-4 text-xs text-muted-foreground text-center">
          {summary.total_tokens_in} in / {summary.total_tokens_out} out
        </div>
      </div>
    </div>
  );
}
