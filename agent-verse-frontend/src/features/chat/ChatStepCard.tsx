/**
 * ChatStepCard — shows a goal execution step with status and expandable tool detail.
 */

import { useState, type JSX } from 'react';
import { ChevronDown, ChevronRight, CheckCircle, Loader2, XCircle } from 'lucide-react';

interface ToolCall {
  name: string;
  input?: string;
  output?: string;
}

interface Props {
  stepName: string;
  status: 'running' | 'complete' | 'failed';
  result?: string;
  toolCalls?: ToolCall[];
}

export function ChatStepCard({ stepName, status, result, toolCalls = [] }: Props): JSX.Element {
  const [expanded, setExpanded] = useState(false);

  const icon =
    status === 'running' ? (
      <Loader2 className="w-4 h-4 text-indigo-500 animate-spin" />
    ) : status === 'complete' ? (
      <CheckCircle className="w-4 h-4 text-green-500" />
    ) : (
      <XCircle className="w-4 h-4 text-red-500" />
    );

  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden my-1">
      <button
        className="flex items-center gap-2 w-full px-3 py-2 text-sm text-left hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        {icon}
        <span className="flex-1 font-medium text-gray-700 dark:text-gray-200">{stepName}</span>
        {toolCalls.length > 0 && (
          <span className="text-xs text-gray-400">{toolCalls.length} tools</span>
        )}
        {expanded ? (
          <ChevronDown className="w-3 h-3 text-gray-400" />
        ) : (
          <ChevronRight className="w-3 h-3 text-gray-400" />
        )}
      </button>

      {expanded && (
        <div className="px-3 pb-3 pt-1 border-t border-gray-100 dark:border-gray-800 space-y-2">
          {result && (
            <p className="text-xs text-gray-500 dark:text-gray-400">{result}</p>
          )}
          {toolCalls.map((tc, i) => (
            <div key={i} className="text-xs bg-gray-50 dark:bg-gray-900 rounded p-2">
              <span className="font-mono font-medium text-indigo-600 dark:text-indigo-400">{tc.name}</span>
              {tc.input && <pre className="mt-1 text-gray-500 whitespace-pre-wrap">{tc.input}</pre>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
