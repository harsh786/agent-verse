/**
 * ChatHITLCard — human-in-the-loop approval card with approve/reject and countdown.
 */

import { type JSX } from 'react';
import { AlertTriangle } from 'lucide-react';

interface Props {
  stepName: string;
  riskLevel?: string;
  timeoutSeconds?: number;
  onApprove?: () => void;
  onReject?: () => void;
}

export function ChatHITLCard({
  stepName,
  riskLevel = 'high',
  timeoutSeconds = 300,
  onApprove,
  onReject,
}: Props): JSX.Element {
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
      <p className="text-xs text-[#5A7494] mb-3">
        Timeout in {Math.floor(timeoutSeconds / 60)} min. This action requires your approval before continuing.
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
