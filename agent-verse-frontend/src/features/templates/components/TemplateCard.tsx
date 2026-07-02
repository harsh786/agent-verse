import type { GoalTemplate } from "@/lib/api/client";
import { FileText, Zap, ChevronRight, Pencil, Trash2 } from "lucide-react";

const DOMAIN_COLORS: Record<string, string> = {
  devops:      "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300",
  engineering: "bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300",
  data:        "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300",
  marketing:   "bg-pink-100 text-pink-800 dark:bg-pink-900/30 dark:text-pink-300",
  sales:       "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300",
  support:     "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300",
  legal:       "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/30 dark:text-indigo-300",
  finance:     "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300",
  general:     "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

interface TemplateCardProps {
  template: GoalTemplate;
  onUse: (template: GoalTemplate) => void;
  onEdit: (template: GoalTemplate) => void;
  onDelete: (id: string) => void;
}

export function TemplateCard({ template, onUse, onEdit, onDelete }: TemplateCardProps) {
  const domainColor = DOMAIN_COLORS[template.domain] ?? DOMAIN_COLORS.general;
  return (
    <div className="bg-card border border-border rounded-xl p-5 hover:border-primary/30 hover:shadow-sm transition-all flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <div className="p-1.5 bg-primary/10 rounded-lg shrink-0">
            <FileText className="h-4 w-4 text-primary" aria-hidden="true" />
          </div>
          <h3 className="text-sm font-semibold text-foreground leading-tight truncate">{template.name}</h3>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${domainColor}`}>
            {template.domain}
          </span>
          <button
            onClick={() => onEdit(template)}
            className="p-1 rounded hover:bg-muted/70 text-muted-foreground hover:text-foreground transition-colors"
            aria-label={`Edit template: ${template.name}`}
            title="Edit"
          >
            <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <button
            onClick={() => onDelete(template.id)}
            className="p-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20 text-muted-foreground hover:text-red-600 dark:hover:text-red-400 transition-colors"
            aria-label={`Delete template: ${template.name}`}
            title="Delete"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>

      {template.description && (
        <p className="text-xs text-muted-foreground line-clamp-2">{template.description}</p>
      )}

      {/* Goal text preview */}
      <p className="text-xs font-mono bg-muted/50 rounded-lg px-2 py-1.5 text-muted-foreground line-clamp-2">
        {template.goal_text}
      </p>

      {/* Parameters */}
      {template.parameters.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {template.parameters.map((p) => (
            <span key={p.name} className="text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded font-mono">
              {`{{${p.name}}}`}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between mt-auto pt-2 border-t border-border">
        <span className="text-xs text-muted-foreground">
          Used {template.use_count} time{template.use_count !== 1 ? "s" : ""}
          {template.version > 1 && <span className="ml-1.5 opacity-60">v{template.version}</span>}
        </span>
        <button
          onClick={() => onUse(template)}
          className="flex items-center gap-1.5 text-xs font-medium text-primary hover:underline"
          aria-label={`Use template: ${template.name}`}
        >
          <Zap className="h-3 w-3" aria-hidden="true" />
          Use template
          <ChevronRight className="h-3 w-3" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
