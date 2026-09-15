/**
 * ChatDiff — unified diff renderer with syntax highlighting by line type.
 */

import { type JSX } from 'react';

interface Props {
  content: string;
  title?: string;
}

export function ChatDiff({ content, title }: Props): JSX.Element {
  const lines = content.split('\n');

  return (
    <div className="rounded-xl border border-border overflow-hidden" role="region" aria-label={title ?? 'Diff'}>
      {title && (
        <div className="px-3 py-2 bg-background border-b border-border text-xs font-medium text-muted-foreground/70">
          {title}
        </div>
      )}
      <pre className="text-xs overflow-x-auto p-0 bg-card m-0">
        {lines.map((line, i) => {
          let cls = 'block px-4 py-0.5 text-muted-foreground/70';
          if (line.startsWith('+') && !line.startsWith('+++')) {
            cls = 'block px-4 py-0.5 bg-green-50 dark:bg-green-950 text-green-700 dark:text-green-300';
          } else if (line.startsWith('-') && !line.startsWith('---')) {
            cls = 'block px-4 py-0.5 bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-300';
          } else if (line.startsWith('@@')) {
            cls = 'block px-4 py-0.5 bg-blue-50 dark:bg-blue-950 text-blue-600 dark:text-blue-400';
          }
          return <span key={i} className={cls}>{line || '\u00a0'}</span>;
        })}
      </pre>
    </div>
  );
}
