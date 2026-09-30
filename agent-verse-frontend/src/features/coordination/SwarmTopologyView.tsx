/** Swarm claims (nodes) and accepted gossip deliveries (edges) written by swarm runs. */
export function SwarmTopologyView({ nodes, edges }: { nodes: Array<Record<string, unknown>>; edges: Array<Record<string, unknown>> }) {
  return (
    <section aria-labelledby="swarm-heading">
      <div className="flex justify-between"><h3 id="swarm-heading" className="font-semibold">Swarm claims</h3><span className="font-mono text-xs text-muted-foreground">{edges.length} links</span></div>
      {nodes.length === 0 && <p className="mt-2 text-sm text-muted-foreground">No swarm run on this session yet.</p>}
      <ul className="mt-3 space-y-2">{nodes.map((node, index) => <li key={String(node.agent_id ?? index)} className="flex flex-wrap justify-between gap-2 rounded-md border p-3 text-sm"><span className="font-mono">{String(node.agent_id ?? node.node_id ?? `agent-${index + 1}`)}</span><span>{String(node.claim_state ?? node.state ?? 'available')}</span>{node.fencing_token != null && <span className="text-muted-foreground">Fence {String(node.fencing_token)}</span>}</li>)}</ul>
      {edges.length > 0 && (
        <ul aria-label="Gossip links" className="mt-3 space-y-1 font-mono text-xs text-muted-foreground">
          {edges.map((edge) => {
            const key = `${String(edge.source)}-${String(edge.target)}-${String(edge.message_type)}`;
            return <li key={key}>{String(edge.source)} → {String(edge.target)} · {String(edge.message_type)} ×{String(edge.count ?? 1)}</li>;
          })}
        </ul>
      )}
    </section>
  );
}
