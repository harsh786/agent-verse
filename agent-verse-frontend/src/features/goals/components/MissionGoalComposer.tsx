/**
 * Mission Goal Composer — premium goal submission with model preview,
 * workflow mode selector, attachment support, and template picker.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  Zap,
  Paperclip,
  BookOpen,
  Ghost,
  Target,
  GitBranch,
  Users,
  ChevronDown,
  Loader2,
  X,
  Brain,
} from 'lucide-react';
import { goalsApi, agentsApi, apiFetch } from '@/lib/api/client';
import { VoiceGoalInput } from '@/components/voice/VoiceGoalInput';
import { CostEstimateWidget } from './CostEstimateWidget';
import { toast } from '@/stores/toast';
import { TemplatePickerModal } from '@/features/templates/components/TemplatePickerModal';

const WORKFLOW_MODES = [
  { id: 'single_agent', label: 'Single Agent', icon: Target, description: 'One focused agent' },
  { id: 'multi_agent', label: 'Multi-Agent', icon: Users, description: 'Parallel execution' },
  { id: 'debate', label: 'Debate', icon: GitBranch, description: 'Consensus through debate' },
] as const;

type WorkflowMode = (typeof WORKFLOW_MODES)[number]['id'];

interface Attachment {
  type: string;
  url?: string;
  data?: string;
  name?: string;
}

interface ModelInfo {
  display_name?: string;
}

interface ModelsResponse {
  models?: ModelInfo[];
}

export function MissionGoalComposer({ onSuccess }: { onSuccess?: (goalId: string) => void }) {
  const navigate = useNavigate();
  const location = useLocation();
  const qc = useQueryClient();

  const prefill = (location.state as { prefillGoal?: string } | null)?.prefillGoal ?? '';
  const [goal, setGoal] = useState(prefill);
  const [dryRun, setDryRun] = useState(false);
  const [agentId, setAgentId] = useState('auto');
  const [workflowMode, setWorkflowMode] = useState<WorkflowMode>('single_agent');
  const [showOptions, setShowOptions] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [showTemplatePicker, setShowTemplatePicker] = useState(false);

  const { data: agents = [] } = useQuery({
    queryKey: ['agents-composer'],
    queryFn: () => agentsApi.list(),
    staleTime: 60_000,
  });

  // Get AI Router model recommendation
  const { data: modelRec } = useQuery({
    queryKey: ['model-rec-planning'],
    queryFn: () =>
      apiFetch<ModelsResponse>('/models?capability=text_generation&limit=1').catch(() => null),
    staleTime: 5 * 60_000,
  });

  const submit = useMutation({
    mutationFn: () =>
      goalsApi.submit({
        goal,
        dry_run: dryRun,
        agent_id: agentId === 'auto' ? undefined : agentId,
        workflow_mode: workflowMode,
        attachments: attachments.length > 0 ? attachments : undefined,
      }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['goals'] });
      const goalId = res.goal_id ?? res.id;
      if (onSuccess) {
        onSuccess(goalId);
      } else {
        navigate(`/goals/${goalId}`);
      }
      setGoal('');
      setAttachments([]);
    },
    onError: (e) => toast({ kind: 'error', message: String(e) }),
  });

  const recommendedModel = modelRec?.models?.[0];

  return (
    <>
      <div className="bg-panel-graphite/80 border border-neural-violet/30 rounded-2xl overflow-hidden shadow-lg shadow-neural-violet/5">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-neural-violet/20">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-verified-green animate-pulse" />
            <span className="text-xs font-medium text-white/70">New Goal</span>
            {recommendedModel?.display_name && (
              <span className="text-[10px] bg-neural-violet/20 text-neural-violet px-1.5 py-0.5 rounded-full flex items-center gap-1">
                <Brain className="h-2.5 w-2.5" />
                {recommendedModel.display_name}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setShowTemplatePicker(true)}
              className="flex items-center gap-1 text-xs text-white/50 hover:text-neural-violet transition-colors"
            >
              <BookOpen className="h-3 w-3" />
              Templates
            </button>
            <button
              type="button"
              onClick={() => navigate('/goals/ghost-run')}
              className="flex items-center gap-1 text-xs text-white/50 hover:text-telemetry-cyan transition-colors"
            >
              <Ghost className="h-3 w-3" />
              Ghost Run
            </button>
          </div>
        </div>

        {/* Goal textarea */}
        <div className="px-5 pt-4 pb-3 relative">
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && goal.trim()) {
                e.preventDefault();
                submit.mutate();
              }
            }}
            rows={3}
            placeholder="Describe your goal in natural language… (⌘↵ to submit)"
            className="w-full bg-transparent text-sm text-white placeholder:text-white/30 focus:outline-none resize-none pr-10"
            aria-label="Goal text"
          />
          <div className="absolute right-5 top-4">
            <VoiceGoalInput
              onTranscript={(t) => setGoal((prev) => (prev ? `${prev} ${t}` : t))}
              disabled={submit.isPending}
            />
          </div>
        </div>

        {/* Attachments */}
        {attachments.length > 0 && (
          <div className="px-5 pb-3 flex flex-wrap gap-1.5">
            {attachments.map((a, i) => (
              <span
                key={i}
                className="flex items-center gap-1 text-xs bg-telemetry-cyan/10 text-telemetry-cyan px-2 py-0.5 rounded-full"
              >
                {a.name ?? a.type}
                <button
                  type="button"
                  onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                  aria-label={`Remove attachment ${a.name ?? a.type}`}
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </span>
            ))}
          </div>
        )}

        {/* Cost estimate */}
        <CostEstimateWidget goal={goal} enabled={goal.length >= 10} />

        {/* Options row */}
        {showOptions && (
          <div className="px-5 py-3 border-t border-white/5 space-y-3">
            {/* Agent selector */}
            <div className="flex items-center gap-3">
              <span className="text-xs text-white/50 w-20">Agent</span>
              <select
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                className="flex-1 bg-command-black border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:border-neural-violet"
              >
                <option value="auto">Auto-select best agent</option>
                {agents.map((a) => (
                  <option key={a.agent_id} value={a.agent_id}>
                    {a.name} ({a.autonomy_mode})
                  </option>
                ))}
              </select>
            </div>

            {/* Workflow mode */}
            <div className="flex items-center gap-3">
              <span className="text-xs text-white/50 w-20">Strategy</span>
              <div className="flex gap-1.5">
                {WORKFLOW_MODES.map(({ id, label, icon: Icon, description }) => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setWorkflowMode(id)}
                    title={description}
                    className={`flex items-center gap-1 px-2.5 py-1 text-xs rounded-lg transition-colors ${
                      workflowMode === id
                        ? 'bg-neural-violet text-white'
                        : 'bg-white/5 text-white/60 hover:bg-white/10'
                    }`}
                  >
                    <Icon className="h-3 w-3" />
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {/* Dry run */}
            <label className="flex items-center gap-2 text-xs text-white/60 cursor-pointer">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(e) => setDryRun(e.target.checked)}
                className="accent-neural-violet"
              />
              Dry run (preview only — no tools executed)
            </label>
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center gap-2 px-5 py-3 border-t border-white/5">
          <label className="flex items-center gap-1.5 text-xs text-white/50 cursor-pointer hover:text-white/70 transition-colors">
            <Paperclip className="h-3.5 w-3.5" />
            Attach
            <input
              type="file"
              accept="image/*,.pdf"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = () => {
                  const b64 = (reader.result as string).split(',')[1];
                  setAttachments((prev) => [
                    ...prev,
                    {
                      type: file.type.startsWith('image/') ? 'image_base64' : 'pdf_base64',
                      data: b64,
                      name: file.name,
                    },
                  ]);
                };
                reader.readAsDataURL(file);
                e.target.value = '';
              }}
            />
          </label>

          <button
            type="button"
            onClick={() => setShowOptions((v) => !v)}
            className="flex items-center gap-1 text-xs text-white/50 hover:text-white/70 transition-colors"
          >
            <ChevronDown
              className={`h-3.5 w-3.5 transition-transform ${showOptions ? 'rotate-180' : ''}`}
            />
            {showOptions ? 'Less' : 'Options'}
          </button>

          <div className="flex-1" />

          <button
            type="button"
            onClick={() => goal.trim() && submit.mutate()}
            disabled={!goal.trim() || submit.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-neural-violet hover:bg-neural-violet/80 disabled:opacity-40 text-white text-xs font-medium rounded-xl transition-colors"
          >
            {submit.isPending ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Launching…
              </>
            ) : (
              <>
                <Zap className="h-3.5 w-3.5" />
                {dryRun ? 'Preview' : 'Launch'}
              </>
            )}
          </button>
        </div>
      </div>

      {/* Template picker modal */}
      {showTemplatePicker && (
        <TemplatePickerModal
          onUseInGoal={(text) => {
            setGoal(text);
            setShowTemplatePicker(false);
          }}
          onClose={() => setShowTemplatePicker(false)}
        />
      )}
    </>
  );
}
