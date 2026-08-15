/**
 * ChatChart — chart renderer using a simple SVG bar chart fallback.
 * In production, swap with Vega-Lite or Recharts.
 */

import { type JSX } from 'react';
import { BarChart2 } from 'lucide-react';

interface BarData {
  label: string;
  value: number;
}

interface Props {
  data?: BarData[];
  title?: string;
  spec?: unknown; // Vega-Lite spec (ignored in fallback)
}

export function ChatChart({ data = [], title }: Props): JSX.Element {
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center gap-2 p-4 border border-gray-200 dark:border-gray-700 rounded-xl text-xs text-gray-400">
        <BarChart2 className="w-4 h-4" />
        No chart data
      </div>
    );
  }

  const max = Math.max(...data.map((d) => d.value), 1);

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4" role="img" aria-label={title ?? 'Chart'}>
      {title && <p className="text-xs font-medium text-gray-600 dark:text-gray-300 mb-3">{title}</p>}
      <div className="space-y-2">
        {data.map((d, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="text-xs text-gray-500 w-24 truncate text-right shrink-0">{d.label}</span>
            <div className="flex-1 bg-gray-100 dark:bg-gray-800 rounded-full h-4 overflow-hidden">
              <div
                className="h-full bg-indigo-500 rounded-full transition-all"
                style={{ width: `${(d.value / max) * 100}%` }}
                role="presentation"
              />
            </div>
            <span className="text-xs text-gray-500 w-10 shrink-0">{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
