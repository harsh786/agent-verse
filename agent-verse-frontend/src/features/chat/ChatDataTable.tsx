/**
 * ChatDataTable — sortable/filterable data table component.
 */

import { useState, type JSX } from 'react';
import { ArrowUpDown } from 'lucide-react';

interface Props {
  data: Record<string, unknown>[];
  title?: string;
}

export function ChatDataTable({ data, title }: Props): JSX.Element {
  const [filter, setFilter] = useState('');
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortAsc, setSortAsc] = useState(true);

  if (!data || data.length === 0) {
    return <p className="text-xs text-[#A0B4CC]">No data to display</p>;
  }

  const keys = Object.keys(data[0]);

  let filtered = filter
    ? data.filter((row) =>
        keys.some((k) => String(row[k] ?? '').toLowerCase().includes(filter.toLowerCase()))
      )
    : data;

  if (sortKey) {
    filtered = [...filtered].sort((a, b) => {
      const av = String(a[sortKey] ?? '');
      const bv = String(b[sortKey] ?? '');
      return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    });
  }

  const toggleSort = (key: string) => {
    if (sortKey === key) setSortAsc((a) => !a);
    else { setSortKey(key); setSortAsc(true); }
  };

  return (
    <div className="rounded-xl border border-white/[0.08] dark:border-gray-700 overflow-hidden">
      {title && (
        <div className="px-3 py-2 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between">
          <span className="text-xs font-medium text-[#5A7494] dark:text-gray-300">{title}</span>
          <input
            className="text-xs border border-white/[0.08] dark:border-gray-700 rounded-lg px-2 py-1 w-40 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            placeholder="Filter…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Filter table"
          />
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs" role="table">
          <thead className="bg-[#0A0F1A] dark:bg-gray-800">
            <tr>
              {keys.map((k) => (
                <th
                  key={k}
                  className="px-3 py-2 text-left font-medium text-[#5A7494] uppercase tracking-wide cursor-pointer hover:text-indigo-600 select-none"
                  scope="col"
                  onClick={() => toggleSort(k)}
                >
                  <span className="flex items-center gap-1">
                    {k}
                    <ArrowUpDown className="w-3 h-3 opacity-40" />
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {filtered.map((row, i) => (
              <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                {keys.map((k) => (
                  <td key={k} className="px-3 py-2 text-[#A0B4CC] dark:text-gray-300">
                    {String(row[k] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="px-3 py-1.5 border-t border-gray-100 dark:border-gray-800 text-xs text-[#A0B4CC]">
        {filtered.length} of {data.length} rows
      </div>
    </div>
  );
}
