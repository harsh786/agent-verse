/**
 * ChatRichOutput — dispatcher for rich output types: table/chart/diff/image/artifact.
 */

import { type JSX } from 'react';

interface Props {
  outputType: 'table' | 'chart' | 'diff' | 'image' | 'text' | 'artifact' | string;
  data?: unknown;
  imageUrl?: string;
  diffContent?: string;
  artifactTitle?: string;
}

function TableOutput({ data }: { data: unknown[] }) {
  if (!data || data.length === 0) return <p className="text-xs text-muted-foreground">No data</p>;
  const keys = Object.keys(data[0] as Record<string, unknown>);
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="min-w-full text-xs" role="table">
        <thead className="bg-background">
          <tr>
            {keys.map((k) => (
              <th key={k} className="px-3 py-2 text-left font-medium text-muted-foreground/70 uppercase tracking-wide" scope="col">
                {k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
          {(data as Record<string, unknown>[]).map((row, i) => (
            <tr key={i} className="hover:bg-muted">
              {keys.map((k) => (
                <td key={k} className="px-3 py-2 text-muted-foreground">
                  {String(row[k] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DiffOutput({ content }: { content: string }) {
  const lines = content.split('\n');
  return (
    <pre className="text-xs rounded-lg border border-border overflow-x-auto p-3 bg-background">
      {lines.map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith('+') && !line.startsWith('+++')
              ? 'text-green-600 dark:text-green-400'
              : line.startsWith('-') && !line.startsWith('---')
              ? 'text-red-600 dark:text-red-400'
              : 'text-muted-foreground/70'
          }
        >
          {line}
        </div>
      ))}
    </pre>
  );
}

function ImageOutput({ url }: { url: string }) {
  return (
    <div className="rounded-lg overflow-hidden border border-border">
      <img
        src={url}
        alt="Generated output"
        className="max-w-full h-auto cursor-zoom-in"
        onClick={() => window.open(url, '_blank')}
      />
    </div>
  );
}

export function ChatRichOutput({ outputType, data, imageUrl, diffContent, artifactTitle }: Props): JSX.Element {
  if (outputType === 'table' && Array.isArray(data)) {
    return <TableOutput data={data} />;
  }
  if (outputType === 'diff' && diffContent) {
    return <DiffOutput content={diffContent} />;
  }
  if (outputType === 'image' && imageUrl) {
    return <ImageOutput url={imageUrl} />;
  }
  if (outputType === 'artifact') {
    return (
      <div className="flex items-center gap-2 px-3 py-2 bg-indigo-50 dark:bg-indigo-950 border border-indigo-200 dark:border-indigo-800 rounded-lg text-xs text-indigo-700 dark:text-indigo-300">
        📄 Artifact: {artifactTitle ?? 'View in panel →'}
      </div>
    );
  }
  return <span className="text-xs text-muted-foreground">Rich output: {outputType}</span>;
}
