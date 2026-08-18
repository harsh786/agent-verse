/**
 * ChatTokenCostBadge — shows per-message token + cost info in a hover tooltip.
 */

import { useState, type JSX } from 'react';
import { Coins } from 'lucide-react';

interface Props {
  tokensIn?: number;
  tokensOut?: number;
  costUsd?: number;
  model?: string;
}

export function ChatTokenCostBadge({ tokensIn = 0, tokensOut = 0, costUsd = 0, model }: Props): JSX.Element {
  const [show, setShow] = useState(false);
  const total = tokensIn + tokensOut;

  return (
    <div className="relative inline-block">
      <button
        className="flex items-center gap-1 text-xs text-[#A0B4CC] hover:text-gray-600 transition-colors"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        aria-label={`${total} tokens, $${costUsd.toFixed(5)}`}
      >
        <Coins className="w-3 h-3" />
        <span>{total}</span>
      </button>

      {show && (
        <div className="absolute bottom-full left-0 mb-1 z-50 w-48 bg-gray-900 text-white text-xs rounded-lg p-2.5 shadow-xl">
          <div className="space-y-1">
            {model && <p className="font-medium text-[#A0B4CC]">{model}</p>}
            <div className="flex justify-between">
              <span className="text-[#A0B4CC]">Input</span>
              <span>{tokensIn} tokens</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#A0B4CC]">Output</span>
              <span>{tokensOut} tokens</span>
            </div>
            <div className="flex justify-between border-t border-gray-700 pt-1 mt-1">
              <span className="text-[#A0B4CC]">Cost</span>
              <span className="text-green-400">${costUsd.toFixed(5)}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
