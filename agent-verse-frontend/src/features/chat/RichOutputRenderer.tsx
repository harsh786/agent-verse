/**
 * RichOutputRenderer — renders a completed assistant message, promoting
 * tabular / chart / image output to the dedicated interactive components
 * (ChatDataTable, ChatChart, ChatImageOutput) while leaving prose as markdown.
 *
 * See richOutput.ts for the (conservative) detection rules.
 */

import { type JSX } from 'react';
import { RichMarkdown } from '@/components/ui/RichMarkdown';
import { ChatDataTable } from './ChatDataTable';
import { ChatChart } from './ChatChart';
import { ChatImageOutput } from './ChatImageOutput';
import { parseRichSegments } from './richOutput';

interface Props {
  content: string;
}

export function RichOutputRenderer({ content }: Props): JSX.Element {
  const segments = parseRichSegments(content);

  return (
    <div className="flex flex-col gap-2" data-testid="rich-output">
      {segments.map((seg, i) => {
        switch (seg.kind) {
          case 'table':
            return <ChatDataTable key={i} data={seg.rows} title={seg.title} />;
          case 'chart':
            return <ChatChart key={i} data={seg.data} title={seg.title} />;
          case 'image':
            return <ChatImageOutput key={i} src={seg.src} alt={seg.alt} />;
          case 'markdown':
          default:
            return <RichMarkdown key={i}>{seg.content}</RichMarkdown>;
        }
      })}
    </div>
  );
}
