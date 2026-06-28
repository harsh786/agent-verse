import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { templatesApi, type GoalTemplate } from "@/lib/api/client";
import { TemplateCard } from "./components/TemplateCard";
import { TemplateInstantiator } from "./components/TemplateInstantiator";
import { Skeleton } from "@/components/ui/Skeleton";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { toast } from "@/stores/toast";
import { Plus, BookOpen, Search, X } from "lucide-react";

const DOMAINS = ["general", "devops", "engineering", "data", "marketing", "sales", "support"];

export function TemplateLibraryPage() {
  const qc = useQueryClient();
  const [domainFilter, setDomainFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState<GoalTemplate | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [newTemplate, setNewTemplate] = useState({ name: "", description: "", goal_text: "", domain: "general" });
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ["templates", domainFilter],
    queryFn: () => templatesApi.list(domainFilter ?? undefined),
    staleTime: 30_000,
  });

  const createMutation = useMutation({
    mutationFn: () => templatesApi.create(newTemplate),
    onSuccess: () => {
      toast({ kind: "success", message: "Template created!" });
      qc.invalidateQueries({ queryKey: ["templates"] });
      setCreateOpen(false);
      setNewTemplate({ name: "", description: "", goal_text: "", domain: "general" });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => templatesApi.delete(id),
    onSuccess: () => {
      toast({ kind: "success", message: "Template deleted." });
      qc.invalidateQueries({ queryKey: ["templates"] });
      setDeleteId(null);
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const filtered = templates.filter((t) => {
    if (search) {
      const q = search.toLowerCase();
      return t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q) || t.goal_text.toLowerCase().includes(q);
    }
    return true;
  });

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <BookOpen className="h-6 w-6 text-primary" aria-hidden="true" />
            Template Library
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Reusable goal patterns with fillable parameters
          </p>
        </div>
        <button
          onClick={() => setCreateOpen(true)}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 transition-opacity"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          New Template
        </button>
      </div>

      {/* Search + filters */}
      <div className="flex flex-wrap gap-3">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none" aria-hidden="true" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search templates…"
            className="w-full pl-9 pr-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
            aria-label="Search templates"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setDomainFilter(null)}
            className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${!domainFilter ? "bg-primary text-primary-foreground border-primary" : "bg-background border-input hover:bg-muted/50"}`}
          >
            All
          </button>
          {DOMAINS.filter((d) => d !== "general").map((d) => (
            <button
              key={d}
              onClick={() => setDomainFilter(d === domainFilter ? null : d)}
              className={`px-3 py-1.5 text-xs rounded-lg border transition-colors capitalize ${d === domainFilter ? "bg-primary text-primary-foreground border-primary" : "bg-background border-input hover:bg-muted/50"}`}
            >
              {d}
            </button>
          ))}
        </div>
      </div>

      {/* Template grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-48 rounded-xl" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-48 text-muted-foreground">
          <BookOpen className="h-10 w-10 opacity-20 mb-2" aria-hidden="true" />
          <p className="text-sm">No templates found</p>
          <button onClick={() => setCreateOpen(true)} className="mt-3 text-xs text-primary hover:underline">
            Create your first template
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((t) => (
            <TemplateCard key={t.id} template={t} onUse={setSelectedTemplate} />
          ))}
        </div>
      )}

      {/* Instantiator modal */}
      {selectedTemplate && (
        <TemplateInstantiator template={selectedTemplate} onClose={() => setSelectedTemplate(null)} />
      )}

      {/* Create modal */}
      {createOpen && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={() => setCreateOpen(false)} aria-hidden="true" />
          <div className="relative bg-card border border-border rounded-xl shadow-xl max-w-lg w-full p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold">New Template</h2>
              <button onClick={() => setCreateOpen(false)} className="text-muted-foreground hover:text-foreground" aria-label="Close">
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium mb-1" htmlFor="tpl-name">Name</label>
                <input id="tpl-name" value={newTemplate.name} onChange={(e) => setNewTemplate((p) => ({ ...p, name: e.target.value }))}
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" placeholder="Deploy microservice…" />
              </div>
              <div>
                <label className="block text-xs font-medium mb-1" htmlFor="tpl-desc">Description</label>
                <input id="tpl-desc" value={newTemplate.description} onChange={(e) => setNewTemplate((p) => ({ ...p, description: e.target.value }))}
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary" placeholder="Optional description" />
              </div>
              <div>
                <label className="block text-xs font-medium mb-1" htmlFor="tpl-goal">
                  Goal template <span className="text-muted-foreground font-normal">(use {`{{parameter}}`} for placeholders)</span>
                </label>
                <textarea id="tpl-goal" rows={3} value={newTemplate.goal_text} onChange={(e) => setNewTemplate((p) => ({ ...p, goal_text: e.target.value }))}
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                  placeholder="Deploy {{service}} to {{environment}} with version {{tag}}" />
              </div>
              <div>
                <label className="block text-xs font-medium mb-1" htmlFor="tpl-domain">Domain</label>
                <select id="tpl-domain" value={newTemplate.domain} onChange={(e) => setNewTemplate((p) => ({ ...p, domain: e.target.value }))}
                  className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary">
                  {DOMAINS.map((d) => <option key={d} value={d} className="capitalize">{d}</option>)}
                </select>
              </div>
            </div>
            <div className="flex gap-3">
              <button onClick={() => createMutation.mutate()} disabled={!newTemplate.name || !newTemplate.goal_text || createMutation.isPending}
                className="flex-1 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity">
                {createMutation.isPending ? "Creating…" : "Create Template"}
              </button>
              <button onClick={() => setCreateOpen(false)} className="px-4 py-2.5 border border-input text-sm rounded-lg hover:bg-muted/50 transition-colors">Cancel</button>
            </div>
          </div>
        </div>
      )}

      <ConfirmModal
        open={!!deleteId}
        title="Delete template?"
        description="This template will be permanently removed. Goals already submitted from this template are not affected."
        confirmLabel="Delete"
        variant="danger"
        isLoading={deleteMutation.isPending}
        onConfirm={() => deleteId && deleteMutation.mutate(deleteId)}
        onCancel={() => setDeleteId(null)}
      />
    </div>
  );
}
