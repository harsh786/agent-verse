import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { templatesApi, type GoalTemplate } from "@/lib/api/client";
import { toast } from "@/stores/toast";
import { X, Play, Eye, Copy } from "lucide-react";

interface TemplateInstantiatorProps {
  template: GoalTemplate;
  onClose: () => void;
}

export function TemplateInstantiator({ template, onClose }: TemplateInstantiatorProps) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [params, setParams] = useState<Record<string, string>>(
    Object.fromEntries(template.parameters.map((p) => [p.name, p.default ?? ""]))
  );

  const preview = template.parameters.reduce((text, p) => {
    const val = params[p.name] || `{{${p.name}}}`;
    return text.replace(new RegExp(`\\{\\{${p.name}\\}\\}`, "g"), val);
  }, template.goal_text);

  const instantiate = useMutation({
    mutationFn: (submit: boolean) =>
      templatesApi.instantiate(template.id, params, submit),
    onSuccess: (data, submit) => {
      if (submit && data.submitted_goal) {
        const submitted = data.submitted_goal;
        const goalId = submitted.goal_id ?? submitted.id;
        toast({ kind: "success", message: "Goal submitted from template!" });
        qc.invalidateQueries({ queryKey: ["goals"] });
        navigate(`/goals/${goalId}`);
        onClose();
      } else {
        toast({ kind: "success", message: "Goal instantiated — ready to submit!" });
      }
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const allFilled = template.parameters.filter((p) => p.required).every((p) => params[p.name]?.trim());

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-card border border-border rounded-xl shadow-xl max-w-lg w-full p-6 space-y-5">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold">{template.name}</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Parameter inputs */}
        <div className="space-y-3">
          {template.parameters.map((p) => (
            <div key={p.name}>
              <label className="block text-xs font-medium mb-1" htmlFor={`param-${p.name}`}>
                <code className="bg-primary/10 text-primary px-1 rounded">{`{{${p.name}}}`}</code>
                {p.required && <span className="text-red-500 ml-1">*</span>}
                {p.description && <span className="text-muted-foreground ml-2">{p.description}</span>}
              </label>
              <input
                id={`param-${p.name}`}
                value={params[p.name] ?? ""}
                onChange={(e) => setParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                placeholder={p.default ?? `Enter ${p.name}…`}
                className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary"
              />
            </div>
          ))}
        </div>

        {/* Preview */}
        <div className="bg-muted/30 rounded-lg p-3">
          <p className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
            <Eye className="h-3 w-3" aria-hidden="true" /> Preview
          </p>
          <p className="text-sm text-foreground">{preview}</p>
        </div>

        <div className="flex gap-3">
          <button
            onClick={() => instantiate.mutate(true)}
            disabled={!allFilled || instantiate.isPending}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            <Play className="h-4 w-4" aria-hidden="true" />
            {instantiate.isPending ? "Submitting…" : "Run Now"}
          </button>
          <button
            onClick={() => {
              navigator.clipboard?.writeText(preview).catch(() => {});
              toast({ kind: "success", message: "Goal text copied to clipboard!" });
            }}
            disabled={!allFilled}
            className="px-4 py-2.5 border border-input text-sm rounded-lg hover:bg-muted/50 transition-colors flex items-center gap-1.5 disabled:opacity-50"
            title="Copy instantiated goal text to clipboard"
          >
            <Copy className="h-4 w-4" aria-hidden="true" />
            Copy
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2.5 border border-input text-sm rounded-lg hover:bg-muted/50 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
