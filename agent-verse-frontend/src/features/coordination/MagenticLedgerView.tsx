const list = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

export function MagenticLedgerView({ ledger }: { ledger: Record<string, unknown> | null }) {
  if (!ledger) return <p className="text-sm text-muted-foreground">Ledger unavailable</p>;
  return (
    <section aria-labelledby="magentic-heading">
      <div className="flex items-center justify-between"><h3 id="magentic-heading" className="font-semibold">Magentic ledger</h3><span className="font-mono text-xs">v{String(ledger.version ?? 0)}</span></div>
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {[['Facts', list(ledger.facts)], ['Assumptions', list(ledger.assumptions)], ['Blockers', list(ledger.blockers)]].map(([title, values]) => (
          <div key={String(title)}><h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{String(title)}</h4><ul className="mt-1 list-disc pl-4 text-sm">{(values as string[]).map(value => <li key={value}>{value}</li>)}</ul></div>
        ))}
      </div>
      <p className="mt-3 font-mono text-xs text-muted-foreground">Stalls: {String(ledger.stall_count ?? 0)} · Next: {String(ledger.next_actor ?? 'unassigned')}</p>
    </section>
  );
}
