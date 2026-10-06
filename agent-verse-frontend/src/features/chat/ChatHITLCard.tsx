/**
 * ChatHITLCard — human-in-the-loop approval card with approve/reject and countdown.
 *
 * Approve/Reject go through the authenticated approvals API. There is no
 * "magic link" here (a03-F056-06): the old one was built from an unsigned
 * random token that the signed (sig + exp) /hitl/:id/approve page rejects.
 * Signed links are only minted server-side, for email notifications.
 */

import { type JSX, useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';

interface Props {
  stepName: string;
  riskLevel?: string;
  timeoutSeconds?: number;
  requestId?: string;
  onApprove?: () => void;
  onReject?: () => void;
}

export function ChatHITLCard({
  stepName,
  riskLevel = 'high',
  timeoutSeconds = 300,
  requestId,
  onApprove,
  onReject,
}: Props): JSX.Element {
  const [remaining, setRemaining] = useState(timeoutSeconds);

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
      <p className="text-sm text-foreground mb-1">
        <strong>Step:</strong> {stepName}
      </p>
      {requestId && (
        <p className="text-xs text-muted-foreground/70 mb-1">ID: {requestId}</p>
      )}
      <p className={`text-xs mb-3 font-mono tabular-nums ${isUrgent ? 'text-red-500 font-semibold' : 'text-muted-foreground/70'}`}>
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
      </div>
    </div>
  );
}

