import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Network, Search, Plus, ChevronRight, Loader2, GitBranch, Database, Cpu, FileText, Brain } from 'lucide-react';
import { toast } from '@/stores/toast';
import { apiFetch } from '@/lib/api/client';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

const NODE_TYPE_ICONS: Record<string, React.ElementType> = {
  entity: GitBranch,
  concept: Brain,
  document: FileText,
  goal: Cpu,
  agent: Cpu,
  tool: Database,
};

const NODE_TYPE_COLORS: Record<string, string> = {
  entity: 'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
  concept: 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300',
  document: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  goal: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
  agent: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  tool: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
};

export function GraphExplorerPage() {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [nodeTypeFilter, setNodeTypeFilter] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<any | null>(null);
  const [extractText, setExtractText] = useState('');
  const [showExtract, setShowExtract] = useState(false);

  const { data: nodesData, isLoading } = useQuery({
    queryKey: ['kg-nodes', nodeTypeFilter, search],
    queryFn: () => {
      const params = new URLSearchParams();
      if (nodeTypeFilter) params.set('node_type', nodeTypeFilter);
      if (search) params.set('search', search);
      params.set('limit', '100');
      return apiFetch<any>(`/knowledge-graph/nodes?${params}`);
    },
    staleTime: 30_000,
  });

  const { data: statsData } = useQuery({
    queryKey: ['kg-stats'],
    queryFn: () => apiFetch<any>('/knowledge-graph/stats'),
    staleTime: 30_000,
  });

  const { data: nodeDetail } = useQuery({
    queryKey: ['kg-node', selectedNode?.node_id],
    queryFn: () => apiFetch<any>(`/knowledge-graph/nodes/${selectedNode.node_id}`),
    enabled: !!selectedNode?.node_id,
    staleTime: 30_000,
  });

  const extractMutation = useMutation({
    mutationFn: (text: string) => apiFetch<any>('/knowledge-graph/extract', {
      method: 'POST',
      body: JSON.stringify({ text, use_llm: true }),
    }),
    onSuccess: (data) => {
      toast({ kind: 'success', message: `Extracted ${data.entities_extracted} entities, ${data.relationships_extracted} relationships` });
      setExtractText('');
      setShowExtract(false);
      qc.invalidateQueries({ queryKey: ['kg-nodes'] });
      qc.invalidateQueries({ queryKey: ['kg-stats'] });
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const rebuildMutation = useMutation({
    mutationFn: () => apiFetch<any>('/knowledge-graph/rebuild', { method: 'DELETE' }),
    onSuccess: () => {
      toast({ kind: 'success', message: 'Graph cleared and ready for rebuild' });
      qc.invalidateQueries({ queryKey: ['kg-nodes'] });
      qc.invalidateQueries({ queryKey: ['kg-stats'] });
    },
  });
  void rebuildMutation; // available for manual graph reset

  const nodes = nodesData?.nodes ?? [];
  const stats = statsData ?? {};
  const NODE_TYPES = ['entity', 'concept', 'document', 'goal', 'tool', 'agent', 'memory', 'artifact', 'workflow'];

  return (
    <JARVISPageShell>
    <JARVISStagger className="flex h-[calc(100vh-6rem)] gap-4">
      {/* Left panel: Node list */}
      <div className="w-80 flex flex-col gap-3 shrink-0">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold flex items-center gap-2">
            <Network className="h-5 w-5 text-[#00D4FF]" />
            Graph Explorer
          </h1>
          <button
            onClick={() => setShowExtract(!showExtract)}
            className="text-xs px-2 py-1 bg-primary text-primary-foreground rounded-lg hover:opacity-90"
          >
            <Plus className="h-3.5 w-3.5 inline mr-1" />
            Extract
          </button>
        </div>

        {/* Stats */}
        {stats.total_nodes !== undefined && (
          <div className="grid grid-cols-2 gap-2">
            <div className="bg-card border border-border rounded-lg p-2.5 text-center">
              <p className="text-lg font-bold text-primary">{stats.total_nodes}</p>
              <p className="text-[10px] text-muted-foreground">Nodes</p>
            </div>
            <div className="bg-card border border-border rounded-lg p-2.5 text-center">
              <p className="text-lg font-bold text-primary">{stats.total_edges ?? 0}</p>
              <p className="text-[10px] text-muted-foreground">Edges</p>
            </div>
          </div>
        )}

        {/* Extract panel */}
        {showExtract && (
          <div className="bg-card border border-border rounded-xl p-4 space-y-3">
            <p className="text-sm font-medium">Extract from Text</p>
            <textarea
              value={extractText}
              onChange={(e) => setExtractText(e.target.value)}
              rows={4}
              placeholder="Paste text to extract entities and relationships..."
              className="w-full px-3 py-2 text-xs border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
            />
            <div className="flex gap-2">
              <button
                onClick={() => extractMutation.mutate(extractText)}
                disabled={!extractText.trim() || extractMutation.isPending}
                className="flex-1 py-2 bg-primary text-primary-foreground text-xs rounded-lg disabled:opacity-50 flex items-center justify-center gap-1"
              >
                {extractMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                Extract
              </button>
              <button onClick={() => setShowExtract(false)} className="px-3 py-2 text-xs border border-input rounded-lg hover:bg-muted/50">
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search nodes..."
            className="w-full pl-9 pr-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>

        {/* Node type filter */}
        <div className="flex flex-wrap gap-1">
          <button
            onClick={() => setNodeTypeFilter(null)}
            className={`px-2 py-0.5 text-[10px] rounded-full border transition-colors ${!nodeTypeFilter ? 'bg-primary text-primary-foreground border-primary' : 'border-input hover:bg-muted/50'}`}
          >
            All
          </button>
          {NODE_TYPES.slice(0, 6).map(t => (
            <button key={t} onClick={() => setNodeTypeFilter(nodeTypeFilter === t ? null : t)}
              className={`px-2 py-0.5 text-[10px] rounded-full border capitalize transition-colors ${nodeTypeFilter === t ? 'bg-primary text-primary-foreground border-primary' : 'border-input hover:bg-muted/50'}`}>
              {t}
            </button>
          ))}
        </div>

        {/* Node list */}
        <div className="flex-1 overflow-y-auto space-y-1.5">
          {isLoading ? (
            Array.from({ length: 5 }).map((_, i) => <div key={i} className="h-14 bg-muted animate-pulse rounded-lg" />)
          ) : nodes.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
              <Network className="h-8 w-8 opacity-20 mb-2" />
              <p className="text-sm">No nodes yet</p>
              <p className="text-xs mt-1">Extract text to populate the graph</p>
            </div>
          ) : nodes.map((node: any) => {
            const Icon = NODE_TYPE_ICONS[node.node_type] ?? GitBranch;
            const colorClass = NODE_TYPE_COLORS[node.node_type] ?? 'bg-muted text-muted-foreground';
            return (
              <button
                key={node.node_id}
                onClick={() => setSelectedNode(node)}
                className={`w-full text-left p-3 rounded-lg border transition-[color,background-color,border-color,opacity,box-shadow,transform] ${selectedNode?.node_id === node.node_id ? 'border-primary bg-primary/5' : 'border-border hover:border-[#00D4FF]/30'}`}
              >
                <div className="flex items-start gap-2">
                  <div className={`p-1 rounded text-[10px] shrink-0 ${colorClass}`}>
                    <Icon className="h-3 w-3" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium truncate">{node.label}</p>
                    <p className="text-[10px] text-muted-foreground capitalize">{node.node_type} · {Math.round(node.confidence * 100)}% confidence</p>
                  </div>
                  <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0 mt-0.5" />
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Right panel: Node detail */}
      <div className="flex-1 bg-card border border-border rounded-xl overflow-hidden">
        {!selectedNode ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
            <Network className="h-16 w-16 opacity-10 mb-4" />
            <p className="text-base font-medium">Select a node to explore</p>
            <p className="text-sm mt-1">Click any node in the list to view its connections and provenance</p>
          </div>
        ) : (
          <div className="h-full overflow-y-auto p-6 space-y-5">
            {/* Node header */}
            <div className="flex items-start gap-4">
              <div className={`p-3 rounded-xl ${NODE_TYPE_COLORS[selectedNode.node_type] ?? 'bg-muted'}`}>
                {(() => { const Icon = NODE_TYPE_ICONS[selectedNode.node_type] ?? GitBranch; return <Icon className="h-6 w-6" />; })()}
              </div>
              <div className="flex-1">
                <h2 className="text-xl font-bold">{selectedNode.label}</h2>
                <div className="flex items-center gap-3 mt-1">
                  <span className="text-xs text-muted-foreground capitalize">{selectedNode.node_type}</span>
                  <span className="text-xs text-muted-foreground">·</span>
                  <span className="text-xs text-muted-foreground">{Math.round(selectedNode.confidence * 100)}% confidence</span>
                </div>
              </div>
            </div>

            {/* Content */}
            {selectedNode.content && (
              <div className="bg-muted/30 rounded-lg p-4">
                <p className="text-xs font-medium text-muted-foreground mb-1">Content</p>
                <p className="text-sm">{selectedNode.content}</p>
              </div>
            )}

            {/* Connections */}
            {nodeDetail?.edges && nodeDetail.edges.length > 0 && (
              <div>
                <p className="text-sm font-semibold mb-3 flex items-center gap-2">
                  <GitBranch className="h-4 w-4 text-[#00D4FF]" />
                  Connections ({nodeDetail.edges.length})
                </p>
                <div className="space-y-2">
                  {nodeDetail.edges.map((edge: any) => (
                    <div key={edge.edge_id} className="flex items-center gap-3 p-3 bg-muted/30 rounded-lg text-sm">
                      <span className="text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded font-mono whitespace-nowrap">
                        {edge.edge_type}
                      </span>
                      <span className="text-xs text-muted-foreground truncate">
                        {edge.source_node_id === selectedNode.node_id ? '→' : '←'} {edge.source_node_id === selectedNode.node_id ? edge.target_node_id : edge.source_node_id}
                      </span>
                      <span className="text-[10px] text-muted-foreground ml-auto">{Math.round(edge.confidence * 100)}%</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Metadata */}
            {selectedNode.metadata && Object.keys(selectedNode.metadata).length > 0 && (
              <div>
                <p className="text-sm font-semibold mb-2">Metadata</p>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(selectedNode.metadata).map(([k, v]) => (
                    <div key={k} className="bg-muted/30 rounded-lg p-2.5">
                      <p className="text-[10px] text-muted-foreground">{k}</p>
                      <p className="text-xs font-medium">{String(v)}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </JARVISStagger>
    </JARVISPageShell>
  );
}
