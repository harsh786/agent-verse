/**
 * RichMarkdown — world-class rendering of LLM/agent output.
 *
 * Renders GitHub-flavored Markdown (tables, task lists, strikethrough,
 * autolinks) plus safe inline HTML (e.g. <br>), with styled tables, links,
 * code blocks, headings and lists — so a result that is a markdown table, a
 * bulleted list, fenced code, or prose all render richly instead of as raw
 * text. Raw HTML is sanitized (rehype-sanitize) to prevent XSS.
 */
import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize from 'rehype-sanitize';
import { Check, Copy } from 'lucide-react';
import { cn } from '@/lib/utils';
import { highlightCode } from './codeHighlight';

interface RichMarkdownProps {
  children: string;
  className?: string;
}

function CodeBlock({ code, language }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard?.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable — no-op */
    }
  };
  return (
    <div className="group relative my-3">
      <button
        type="button"
        onClick={handleCopy}
        aria-label={copied ? 'Copied' : 'Copy code'}
        className="absolute right-2 top-2 z-10 rounded-md border border-border bg-background/80 p-1.5 text-muted-foreground opacity-0 transition-opacity hover:text-foreground focus:opacity-100 group-hover:opacity-100"
      >
        {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      <pre className="overflow-x-auto rounded-lg border border-border bg-muted/40 p-3 pr-12 text-xs leading-relaxed">
        <code className={cn('block font-mono', language && `language-${language}`)}>
          {highlightCode(code)}
        </code>
      </pre>
    </div>
  );
}

export function RichMarkdown({ children, className }: RichMarkdownProps) {
  return (
    <div className={cn('rich-md text-sm leading-relaxed text-foreground/90 break-words', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // rehypeRaw parses inline HTML (<br>, <b>…); rehypeSanitize then strips
        // anything unsafe. Order matters: raw first, sanitize second.
        rehypePlugins={[rehypeRaw, rehypeSanitize]}
        components={{
          // Scrollable, bordered, striped tables — the common "result is a table" case.
          table: ({ node: _n, ...props }) => (
            <div className="my-3 overflow-x-auto rounded-lg border border-border">
              <table className="w-full border-collapse text-xs" {...props} />
            </div>
          ),
          thead: ({ node: _n, ...props }) => <thead className="bg-muted/60" {...props} />,
          th: ({ node: _n, ...props }) => (
            <th
              className="border-b border-border px-3 py-2 text-left font-semibold text-foreground whitespace-nowrap"
              {...props}
            />
          ),
          td: ({ node: _n, ...props }) => (
            <td className="border-b border-border/60 px-3 py-2 align-top" {...props} />
          ),
          tr: ({ node: _n, ...props }) => <tr className="even:bg-muted/20" {...props} />,
          a: ({ node: _n, ...props }) => (
            <a
              className="text-sky-500 hover:text-sky-400 underline underline-offset-2 break-all"
              target="_blank"
              rel="noopener noreferrer"
              {...props}
            />
          ),
          h1: ({ node: _n, ...props }) => <h1 className="text-lg font-bold mt-4 mb-2" {...props} />,
          h2: ({ node: _n, ...props }) => <h2 className="text-base font-bold mt-4 mb-2" {...props} />,
          h3: ({ node: _n, ...props }) => <h3 className="text-sm font-semibold mt-3 mb-1.5" {...props} />,
          p: ({ node: _n, ...props }) => <p className="my-2" {...props} />,
          ul: ({ node: _n, ...props }) => <ul className="list-disc pl-5 my-2 space-y-1" {...props} />,
          ol: ({ node: _n, ...props }) => <ol className="list-decimal pl-5 my-2 space-y-1" {...props} />,
          li: ({ node: _n, ...props }) => <li className="marker:text-muted-foreground" {...props} />,
          blockquote: ({ node: _n, ...props }) => (
            <blockquote className="border-l-2 border-border pl-3 my-2 italic text-muted-foreground" {...props} />
          ),
          hr: ({ node: _n, ...props }) => <hr className="my-4 border-border" {...props} />,
          code: ({ node: _n, className: cls, children: c, ...props }) => {
            const text = Array.isArray(c) ? c.join('') : String(c ?? '');
            const isBlock = /language-/.test(cls || '') || text.includes('\n');
            if (isBlock) {
              const language = /language-(\w+)/.exec(cls || '')?.[1];
              return <CodeBlock code={text.replace(/\n$/, '')} language={language} />;
            }
            return (
              <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em]" {...props}>
                {c}
              </code>
            );
          },
          // CodeBlock renders its own <pre>; unwrap markdown's wrapper to avoid
          // a nested/doubled <pre>.
          pre: ({ node: _n, ...props }) => <>{props.children}</>,
          img: ({ node: _n, ...props }) => (
            <img className="my-3 max-w-full rounded-lg border border-border" {...props} />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
