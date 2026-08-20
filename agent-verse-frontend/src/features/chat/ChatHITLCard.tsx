/**
 * ChatHITLCard — human-in-the-loop approval card with approve/reject and countdown.
 */

import { type JSX, useEffect, useState } from 'react';
import { AlertTriangle, Copy, Check } from 'lucide-react';

interface Props {
  stepName: string;
  riskLevel?: string;
  timeoutSeconds?: number;
  requestId?: string;
  approvalToken?: string;
  onApprove?: () => void;
  onReject?: () => void;
}

export function ChatHITLCard({
  stepName,
  riskLevel = 'high',
  timeoutSeconds = 300,
  requestId,
  approvalToken,
  onApprove,
  onReject,
}: Props): JSX.Element {
  const [remaining, setRemaining] = useState(timeoutSeconds);
  const [copied, setCopied] = useState(false);

  // Live countdown — decrements every second
  useEffect(() => {
    setRemaining(timeoutSeconds);
    const id = setInterval(() => {
      setRemaining((s) => {
        if (s <= 1) { clearInterval(id); return 0; }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [timeoutSeconds]);

  const isUrgent = remaining <= 60;
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  const timeLabel = minutes > 0
    ? `${minutes}m ${seconds.toString().padStart(2, '0')}s`
    : `${seconds}s`;

  // G-18: Magic link from approvalToken
  const magicLink = requestId && approvalToken
    ? `${window.location.origin}/hitl/${requestId}/approve?token=${approvalToken}`
    : null;

  async function handleCopyLink() {
    if (!magicLink) return;
    await navigator.clipboard.writeText(magicLink);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="border border-orange-200 dark:border-orange-800 bg-orange-50 dark:bg-orange-950 rounded-xl p-4 my-2">
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle className="w-4 h-4 text-orange-600" />
        <span className="text-xs font-semibold text-orange-700 dark:text-orange-300 uppercase tracking-wide">
          Human approval required
        </span>
        <span className="ml-auto text-xs text-orange-500 bg-orange-100 dark:bg-orange-900 px-2 py-0.5 rounded-full">
          {riskLevel} risk
        </span>
      </div>
      <p className="text-sm text-[#F0F6FF] dark:text-gray-200 mb-1">
        <strong>Step:</strong> {stepName}
      </p>
      {requestId && (
        <p className="text-xs text-[#5A7494] mb-1">ID: {requestId}</p>
      )}
      <p className={`text-xs mb-3 font-mono tabular-nums ${isUrgent ? 'text-red-500 font-semibold' : 'text-[#5A7494]'}`}>
        {remaining === 0 ? 'Timed out' : `Expires in ${timeLabel}`}
      </p>
      <div className="flex gap-2">
        <button
          className="flex-1 py-2 bg-green-600 hover:bg-green-700 text-white text-sm rounded-lg transition-colors font-medium"
          onClick={onApprove}
          aria-label="Approve action"
        >
          Approve
        </button>
        <button
          className="flex-1 py-2 bg-red-100 hover:bg-red-200 dark:bg-red-900 dark:hover:bg-red-800 text-red-700 dark:text-red-300 text-sm rounded-lg transition-colors font-medium"
          onClick={onReject}
          aria-label="Reject action"
        >
          Reject
        </button>
        {magicLink && (
          <button
            className="px-3 py-2 bg-orange-100 hover:bg-orange-200 dark:bg-orange-900 dark:hover:bg-orange-800 text-orange-700 dark:text-orange-300 text-xs rounded-lg transition-colors"
            onClick={handleCopyLink}
            aria-label="Copy magic approve link"
            title="Copy one-click approve link"
          >
            {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
        )}
      </div>
    </div>
  );
}

