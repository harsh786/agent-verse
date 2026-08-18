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
      <div className="flex items-center gap-2 p-4 border border-white/[0.08] dark:border-gray-700 rounded-xl text-xs text-[#A0B4CC]">
        <BarChart2 className="w-4 h-4" />
        No chart data
      </div>
    );
  }

  const max = Math.max(...data.map((d) => d.value), 1);

  return (
    <div className="rounded-xl border border-white/[0.08] dark:border-gray-700 p-4" role="img" aria-label={title ?? 'Chart'}>
      {title && <p className="text-xs font-medium text-[#5A7494] dark:text-gray-300 mb-3">{title}</p>}
      <div className="space-y-2">
        {data.map((d, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="text-xs text-[#5A7494] w-24 truncate text-right shrink-0">{d.label}</span>
            <div className="flex-1 bg-[#0F1826] dark:bg-gray-800 rounded-full h-4 overflow-hidden">
              <div
                className="h-full bg-indigo-500 rounded-full transition-[color,background-color,border-color,opacity,box-shadow,transform]"
                style={{ width: `${(d.value / max) * 100}%` }}
                role="presentation"
              />
            </div>
            <span className="text-xs text-[#5A7494] w-10 shrink-0">{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
