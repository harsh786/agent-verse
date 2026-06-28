/**
 * AgentPersonalityPage — visual personality configuration for agents.
 * Sliders map to real agent config (model, max_iterations, autonomy_mode).
 */
import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { agentsApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/stores/toast";
import { ArrowLeft, Sliders, Save } from "lucide-react";

interface PersonalitySlider {
  id: string;
  label: string;
  leftLabel: string;
  rightLabel: string;
  description: string;
  min: number;
  max: number;
  step: number;
}

const SLIDERS: PersonalitySlider[] = [
  {
    id: "autonomy",
    label: "Autonomy",
    leftLabel: "Supervised",
    rightLabel: "Fully Autonomous",
    description: "Controls how much the agent acts independently vs. requesting approvals",
    min: 0, max: 100, step: 25,
  },
  {
    id: "thoroughness",
    label: "Thoroughness",
    leftLabel: "Fast",
    rightLabel: "Thorough",
    description: "Balances execution speed against depth of verification",
    min: 0, max: 100, step: 10,
  },
  {
    id: "creativity",
    label: "Strategy",
    leftLabel: "Deterministic",
    rightLabel: "Creative",
    description: "Affects model temperature and tool selection diversity",
    min: 0, max: 100, step: 10,
  },
  {
    id: "cost",
    label: "Quality vs Cost",
    leftLabel: "Cost-Optimized",
    rightLabel: "Quality-First",
    description: "Affects model selection between efficient and powerful options",
    min: 0, max: 100, step: 25,
  },
];

function sliderValuesToConfig(values: Record<string, number>) {
  // Map slider values to real agent config
  const autonomyMode =
    values.autonomy >= 75 ? "fully-autonomous" :
    values.autonomy >= 50 ? "bounded-autonomous" :
    values.autonomy >= 25 ? "supervised" : "manual";

  const maxIterations = Math.round(5 + (values.thoroughness / 100) * 15);

  const model =
    values.cost >= 75 ? "claude-opus-4" :
    values.cost >= 50 ? "claude-sonnet-4-5" :
    "claude-haiku-3-5";

  return { autonomy_mode: autonomyMode, max_iterations: maxIterations, model_override: model };
}

function configToSliderValues(agent: Record<string, unknown>): Record<string, number> {
  const modeMap: Record<string, number> = {
    "fully-autonomous": 100, "bounded-autonomous": 50, "supervised": 25, "manual": 0,
  };
  const autonomy = modeMap[(agent.autonomy_mode as string) ?? "bounded-autonomous"] ?? 50;
  const thoroughness = Math.round(((((agent.max_iterations as number) ?? 10) - 5) / 15) * 100);
  const costMap: Record<string, number> = {
    "claude-opus-4": 100, "claude-sonnet-4-5": 50, "claude-haiku-3-5": 25,
  };
  const cost = costMap[(agent.model_override as string) ?? ""] ?? 50;
  return { autonomy, thoroughness, creativity: 50, cost };
}

export function AgentPersonalityPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [values, setValues] = useState<Record<string, number>>({
    autonomy: 50, thoroughness: 50, creativity: 50, cost: 50,
  });
  const [initialized, setInitialized] = useState(false);

  const { data: agent, isLoading } = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => agentsApi.get(agentId!),
    enabled: !!agentId,
  });

  useEffect(() => {
    if (agent && !initialized) {
      const agentAsMap: Record<string, unknown> = {
        autonomy_mode: agent.autonomy_mode,
        max_iterations: (agent as unknown as Record<string, unknown>).max_iterations,
        model_override: (agent as unknown as Record<string, unknown>).model_override,
      };
      setValues(configToSliderValues(agentAsMap));
      setInitialized(true);
    }
  }, [agent, initialized]);

  const save = useMutation({
    mutationFn: () => {
      const config = sliderValuesToConfig(values);
      return agentsApi.update(agentId!, config);
    },
    onSuccess: () => {
      toast({ kind: "success", message: "Personality saved!" });
      qc.invalidateQueries({ queryKey: ["agent", agentId] });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const previewConfig = sliderValuesToConfig(values);

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="flex items-center gap-3">
        <button onClick={() => navigate(`/agents/${agentId}`)} className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground hover:text-foreground transition-colors" aria-label="Back">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <Sliders className="h-5 w-5 text-primary" aria-hidden="true" />
            Agent Personality
          </h1>
          <p className="text-sm text-muted-foreground">{isLoading ? "Loading…" : agent?.name ?? agentId}</p>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-xl" />)}</div>
      ) : (
        <div className="space-y-4">
          {SLIDERS.map((slider) => (
            <div key={slider.id} className="bg-card border border-border rounded-xl p-5">
              <div className="flex items-center justify-between mb-1">
                <label className="text-sm font-medium" htmlFor={`slider-${slider.id}`}>{slider.label}</label>
                <span className="text-xs text-muted-foreground">{values[slider.id]}%</span>
              </div>
              <p className="text-xs text-muted-foreground mb-3">{slider.description}</p>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground shrink-0">{slider.leftLabel}</span>
                <input
                  id={`slider-${slider.id}`}
                  type="range"
                  min={slider.min}
                  max={slider.max}
                  step={slider.step}
                  value={values[slider.id]}
                  onChange={(e) => setValues((v) => ({ ...v, [slider.id]: Number(e.target.value) }))}
                  className="flex-1 accent-primary"
                  aria-valuemin={slider.min}
                  aria-valuemax={slider.max}
                  aria-valuenow={values[slider.id]}
                />
                <span className="text-xs text-muted-foreground shrink-0">{slider.rightLabel}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Config preview */}
      <div className="bg-muted/30 border border-border rounded-xl p-4">
        <p className="text-xs font-medium text-muted-foreground mb-2">Generated config</p>
        <div className="grid grid-cols-3 gap-3 text-xs">
          <div>
            <p className="text-muted-foreground">Mode</p>
            <p className="font-mono font-medium text-foreground">{previewConfig.autonomy_mode}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Max iterations</p>
            <p className="font-mono font-medium text-foreground">{previewConfig.max_iterations}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Model</p>
            <p className="font-mono font-medium text-foreground">{previewConfig.model_override}</p>
          </div>
        </div>
      </div>

      <button
        onClick={() => save.mutate()}
        disabled={save.isPending || isLoading}
        className="flex items-center gap-2 px-6 py-2.5 bg-primary text-primary-foreground text-sm font-medium rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        <Save className="h-4 w-4" aria-hidden="true" />
        {save.isPending ? "Saving…" : "Save Personality"}
      </button>
    </div>
  );
}
