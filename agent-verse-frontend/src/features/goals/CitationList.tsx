

interface Citation {
  text: string;
  source: string;
  step?: number;
}

interface CitationListProps {
  citations: Citation[];
  citedAnswer?: string;
}

export function CitationList({ citations, citedAnswer }: CitationListProps) {
  if (!citations || citations.length === 0) return null;

  return (
    <div className="mt-4 rounded-lg border bg-card p-4">
      <h3 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
        <span>📎</span> Sources & Citations
      </h3>

      {citedAnswer && (
        <div className="mb-3 p-3 bg-muted/50 rounded text-sm text-foreground">
          {citedAnswer}
        </div>
      )}

      <ul className="space-y-2">
        {citations.map((c, i) => (
          <li key={i} className="flex gap-2 text-xs">
            <span className="flex-shrink-0 w-5 h-5 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold">
              {c.step ?? i + 1}
            </span>
            <div>
              <span className="text-muted-foreground font-mono">[{c.source}]</span>
              {' '}
              <span className="text-foreground">{c.text}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
