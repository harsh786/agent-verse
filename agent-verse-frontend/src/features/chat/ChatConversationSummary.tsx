/**
 * ChatConversationSummary — /summarize result card with topics + outstanding items.
 */

import { type JSX } from 'react';
import { FileText } from 'lucide-react';

interface SummaryData {
  topic?: string;
  what_we_did?: string[];
  outstanding?: string[];
  message_count?: number;
}

interface Props {
  summary: string | SummaryData;
}

export function ChatConversationSummary({ summary }: Props): JSX.Element {
  const isStructured = typeof summary === 'object';
  const data = isStructured ? summary : null;

  return (
    <div className="border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950 rounded-xl p-4 my-2">
      <div className="flex items-center gap-2 mb-3">
        <FileText className="w-4 h-4 text-blue-600" />
        <span className="text-sm font-medium text-blue-700 dark:text-blue-300">Conversation Summary</span>
      </div>

      {isStructured && data ? (
        <div className="space-y-3">
          {data.topic && (
            <p className="text-sm font-medium text-[#F0F6FF] dark:text-gray-200">{data.topic}</p>
          )}
          {data.what_we_did && data.what_we_did.length > 0 && (
            <div>
              <p className="text-xs font-medium text-[#5A7494] mb-1">What we did:</p>
              <ul className="space-y-0.5">
                {data.what_we_did.map((item, i) => (
                  <li key={i} className="text-xs text-[#A0B4CC] dark:text-gray-300 flex items-start gap-1.5">
                    <span className="text-green-500 mt-0.5">✓</span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.outstanding && data.outstanding.length > 0 && (
            <div>
              <p className="text-xs font-medium text-[#5A7494] mb-1">Outstanding:</p>
              <ul className="space-y-0.5">
                {data.outstanding.map((item, i) => (
                  <li key={i} className="text-xs text-[#A0B4CC] dark:text-gray-300 flex items-start gap-1.5">
                    <span className="text-yellow-500 mt-0.5">○</span>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : (
        <p className="text-sm text-[#A0B4CC] dark:text-gray-300">{String(summary)}</p>
      )}
    </div>
  );
}
