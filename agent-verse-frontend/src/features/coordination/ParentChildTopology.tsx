export function ParentChildTopology({ nodes }: { nodes: Array<Record<string, unknown>> }) {
  return (
    <section aria-labelledby="topology-heading">
      <h2 id="topology-heading" className="text-lg font-semibold">Parent and child topology</h2>
      {nodes.length === 0 ? <p className="mt-3 text-sm text-muted-foreground">No child executions.</p> : (
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {nodes.map((node, index) => (
            <li key={String(node.execution_id ?? node.agent_id ?? index)} aria-current={node.current ? 'step' : undefined} className="rounded-md border p-3 text-sm">
              <p className="font-mono">{String(node.safe_summary ?? node.agent_id ?? `execution-${index + 1}`)}</p>
              <p className="mt-1 text-xs text-muted-foreground">{String(node.state ?? 'pending')}{node.current ? ' · current focus' : ''}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
