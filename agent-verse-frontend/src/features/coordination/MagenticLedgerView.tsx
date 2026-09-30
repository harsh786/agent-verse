const list = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];

/** The Magentic progress ledger (LedgerRevision) written by a Magentic pattern run. */
export function MagenticLedgerView({ ledger }: { ledger: Record<string, unknown> | null }) {
  if (!ledger) {
    return (
      <section aria-labelledby="magentic-heading">
        <h3 id="magentic-heading" className="font-semibold">Magentic ledger</h3>
        <p className="mt-2 text-sm text-muted-foreground">No Magentic run on this session yet. Start one under “Run a pattern”.</p>
      </section>
    );
  }
  const assignments = list(ledger.assignment_history);
  const sections: Array<[string, string[]]> = [
    ['Open work', list(ledger.open_work)],
    ['Completed', list(ledger.completed_work)],
    ['Verified facts', list(ledger.verified_facts)],
    ['Blockers', list(ledger.blockers)],
  ];
  return (
    <section aria-labelledby="magentic-heading">
      <div className="flex items-center justify-between"><h3 id="magentic-heading" className="font-semibold">Magentic ledger</h3><span className="font-mono text-xs">v{String(ledger.version ?? 0)}</span></div>
      {typeof ledger.objective === 'string' && <p className="mt-2 text-sm">{ledger.objective}</p>}
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {sections.map(([title, values]) => (
          <div key={title}><h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</h4><ul className="mt-1 list-disc pl-4 text-sm">{values.map(value => <li key={value}>{value}</li>)}</ul></div>
        ))}
      </div>
      <p className="mt-3 font-mono text-xs text-muted-foreground">Resets: {String(ledger.reset_count ?? 0)} · Last: {assignments[assignments.length - 1] ?? 'unassigned'}</p>
    </section>
  );
}
