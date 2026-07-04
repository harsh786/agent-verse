/**
 * TemplatePickerModal
 *
 * Inline modal shown when the user clicks "Templates" on the Goals page.
 * Lets the user browse / search templates and pick one to pre-fill the
 * goal textarea — without navigating away from the Goals page.
 *
 * Flow:
 *   1. Modal opens with searchable template grid.
 *   2. User clicks "Use in Goal" on a card → TemplateInstantiator opens.
 *   3. User fills {{params}} → clicks "Use in Goal" (not "Run Now").
 *   4. `onUseInGoal(instantiatedText)` is called → modal closes →
 *      GoalsListPage pre-fills its textarea.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { templatesApi, type GoalTemplate } from "@/lib/api/client";
import { TemplateCard } from "./TemplateCard";
import { TemplateInstantiator } from "./TemplateInstantiator";
import { Skeleton } from "@/components/ui/Skeleton";
import { BookOpen, Search, X } from "lucide-react";

const DOMAINS = ["devops", "engineering", "data", "marketing", "sales", "support", "legal", "finance"];

interface TemplatePickerModalProps {
  /** Called with the fully-interpolated goal text when user clicks "Use in Goal". */
  onUseInGoal: (goalText: string) => void;
  onClose: () => void;
}

export function TemplatePickerModal({ onUseInGoal, onClose }: TemplatePickerModalProps) {
  const [domainFilter, setDomainFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selectedTemplate, setSelectedTemplate] = useState<GoalTemplate | null>(null);

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ["templates", domainFilter],
    queryFn: () => templatesApi.list(domainFilter ?? undefined),
    staleTime: 30_000,
  });

  const filtered = templates.filter((t) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      t.name.toLowerCase().includes(q) ||
      t.description.toLowerCase().includes(q) ||
      t.goal_text.toLowerCase().includes(q)
    );
  });

  // When a template is instantiated and the user clicks "Use in Goal"
  const handleUseInGoal = (text: string) => {
    onUseInGoal(text);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center pt-12 p-4">
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="relative bg-card border border-border rounded-xl shadow-2xl w-full max-w-3xl max-h-[80vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <div className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-primary" aria-hidden="true" />
            <h2 className="text-base font-semibold">Choose a template</h2>
            <span className="text-xs text-muted-foreground">
              — select one to pre-fill your goal
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground transition-colors"
            aria-label="Close template picker"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Search + domain filters */}
        <div className="px-5 py-3 border-b border-border space-y-2">
          <div className="relative">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none"
              aria-hidden="true"
            />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search templates…"
              className="w-full pl-9 pr-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
              aria-label="Search templates"
              autoFocus
            />
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setDomainFilter(null)}
              className={`px-2.5 py-1 text-xs rounded-lg border transition-colors ${
                !domainFilter
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-background border-input hover:bg-muted/50"
              }`}
            >
              All
            </button>
            {DOMAINS.map((d) => (
              <button
                key={d}
                onClick={() => setDomainFilter(d === domainFilter ? null : d)}
                className={`px-2.5 py-1 text-xs rounded-lg border transition-colors capitalize ${
                  d === domainFilter
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-background border-input hover:bg-muted/50"
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>

        {/* Template grid (scrollable) */}
        <div className="flex-1 overflow-y-auto p-5">
          {isLoading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-44 rounded-xl" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
              <BookOpen className="h-8 w-8 opacity-20 mb-2" aria-hidden="true" />
              <p className="text-sm">No templates found</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {filtered.map((t) => (
                <TemplateCard
                  key={t.id}
                  template={t}
                  onUse={setSelectedTemplate}
                  // Edit/delete not available from picker — those live on /templates
                  onEdit={() => {}}
                  onDelete={() => {}}
                  pickerMode
                />
              ))}
            </div>
          )}
        </div>

        {/* Footer hint */}
        <div className="px-5 py-3 border-t border-border text-xs text-muted-foreground flex items-center justify-between">
          <span>
            {filtered.length} template{filtered.length !== 1 ? "s" : ""}
          </span>
          <a
            href="/templates"
            className="text-primary hover:underline"
            target="_blank"
            rel="noreferrer"
          >
            Manage templates →
          </a>
        </div>
      </div>

      {/* TemplateInstantiator shown on top */}
      {selectedTemplate && (
        <TemplateInstantiator
          template={selectedTemplate}
          onClose={() => setSelectedTemplate(null)}
          onUseInGoal={handleUseInGoal}
        />
      )}
    </div>
  );
}
