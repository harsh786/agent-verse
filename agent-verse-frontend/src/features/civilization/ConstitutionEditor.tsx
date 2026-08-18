/**
 * ConstitutionEditor — form-based governance editor.
 *
 * Replaces raw JSON textarea with:
 * - Labelled fields for every governance knob
 * - Sliders for numeric values
 * - Toggles for booleans
 * - Collapsible raw JSON preview
 * - Inline validation
 */
import { useState, useCallback } from 'react';
import { ChevronDown, ChevronRight, Save, RefreshCw, AlertTriangle, Check } from 'lucide-react';
import type { CivilizationConstitution } from '../../lib/api/civilizationApi';

interface Props {
  constitution: CivilizationConstitution;
  onSave: (c: CivilizationConstitution) => Promise<void>;
  readOnly?: boolean;
}

interface FieldDef {
  key: keyof CivilizationConstitution;
  label: string;
  description: string;
  type: 'slider' | 'toggle' | 'number' | 'select';
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  options?: { value: string; label: string }[];
}

const FIELDS: FieldDef[] = [
  {
    key: 'max_depth',
    label: 'Max Spawn Depth',
    description: 'How many generations of sub-agents can be spawned',
    type: 'slider', min: 1, max: 10, step: 1,
  },
  {
    key: 'max_total_agents',
    label: 'Max Total Agents',
    description: 'Hard ceiling on concurrent society members',
    type: 'slider', min: 1, max: 50, step: 1,
  },
  {
    key: 'max_concurrent_agents',
    label: 'Max Concurrent',
    description: 'Agents executing at the same time',
    type: 'slider', min: 1, max: 20, step: 1,
  },
  {
    key: 'total_budget_usd',
    label: 'Total Budget',
    description: 'Maximum USD spend for this civilization',
    type: 'slider', min: 1, max: 500, step: 1, unit: '$',
  },
  {
    key: 'per_agent_budget_usd',
    label: 'Per-Agent Budget',
    description: 'Maximum each agent can spend',
    type: 'slider', min: 0.1, max: 100, step: 0.1, unit: '$',
  },
  {
    key: 'budget_decay',
    label: 'Budget Decay',
    description: 'Fraction of budget passed to sub-agents (0–1)',
    type: 'slider', min: 0, max: 1, step: 0.05,
  },
  {
    key: 'reputation_floor',
    label: 'Reputation Floor',
    description: 'Minimum reputation before an agent is retired',
    type: 'slider', min: 0, max: 1, step: 0.05,
  },
  {
    key: 'spawn_rate_limit_per_min',
    label: 'Spawn Rate Limit / min',
    description: 'Max spawns per minute to prevent runaway growth',
    type: 'slider', min: 0, max: 30, step: 1,
  },
  {
    key: 'idle_ttl_seconds',
    label: 'Idle TTL (seconds)',
    description: 'Idle agents are retired after this duration',
    type: 'slider', min: 60, max: 14400, step: 60,
  },
  {
    key: 'autonomy_ceiling',
    label: 'Autonomy Ceiling',
    description: 'Maximum autonomy mode for spawned agents',
    type: 'select',
    options: [
      { value: 'supervised', label: 'Supervised' },
      { value: 'bounded-autonomous', label: 'Bounded Autonomous' },
      { value: 'fully-autonomous', label: 'Fully Autonomous' },
    ],
  },
  {
    key: 'high_risk_requires_hitl',
    label: 'HITL for High-Risk',
    description: 'Require human approval for write_high tool calls',
    type: 'toggle',
  },
];

function SliderField({ def, value, onChange, readOnly }: {
  def: FieldDef;
  value: number;
  onChange: (v: number) => void;
  readOnly: boolean;
}) {
  const pct = def.max && def.min !== undefined
    ? ((value - def.min) / (def.max - def.min)) * 100
    : 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-slate-300">{def.label}</label>
        <span className="text-xs font-mono font-semibold text-indigo-300">
          {def.unit}{typeof value === 'number' ? value.toFixed(def.step! < 1 ? 2 : 0) : value}
        </span>
      </div>
      <div className="relative h-4 flex items-center">
        <div className="absolute inset-x-0 h-1.5 bg-white/8 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-150"
            style={{ width: `${pct}%` }}
          />
        </div>
        <input
          type="range"
          min={def.min}
          max={def.max}
          step={def.step}
          value={value}
          onChange={e => onChange(parseFloat(e.target.value))}
          disabled={readOnly}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
          style={{ zIndex: 1 }}
          aria-label={def.label}
        />
        {/* Thumb */}
        <div
          className="absolute w-3.5 h-3.5 bg-white rounded-full shadow-lg border-2 border-indigo-400 pointer-events-none transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-150"
          style={{ left: `calc(${pct}% - 7px)` }}
        />
      </div>
      <p className="text-[10px] text-slate-600 leading-tight">{def.description}</p>
    </div>
  );
}

function ToggleField({ def, value, onChange, readOnly }: {
  def: FieldDef;
  value: boolean;
  onChange: (v: boolean) => void;
  readOnly: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="text-xs font-medium text-slate-300 leading-snug">{def.label}</p>
        <p className="text-[10px] text-slate-600 mt-0.5 leading-tight">{def.description}</p>
      </div>
      <button
        role="switch"
        aria-checked={value}
        aria-label={def.label}
        onClick={() => !readOnly && onChange(!value)}
        disabled={readOnly}
        className={`relative flex-shrink-0 w-10 h-5.5 rounded-full transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 ${
          value ? 'bg-indigo-500' : 'bg-white/10'
        } disabled:opacity-40 disabled:cursor-not-allowed`}
        style={{ height: '22px', width: '40px' }}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-4.5 h-4.5 bg-white rounded-full shadow transition-transform duration-200 ${
            value ? 'translate-x-[18px]' : 'translate-x-0'
          }`}
          style={{ width: '18px', height: '18px' }}
        />
      </button>
    </div>
  );
}

function SelectField({ def, value, onChange, readOnly }: {
  def: FieldDef;
  value: string;
  onChange: (v: string) => void;
  readOnly: boolean;
}) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-slate-300">{def.label}</label>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={readOnly}
        className="
          w-full text-xs rounded-lg px-2.5 py-1.5
          bg-white/5 border border-white/10 text-slate-200
          focus:outline-none focus:border-indigo-500/50
          disabled:opacity-40 disabled:cursor-not-allowed
          appearance-none cursor-pointer
        "
        aria-label={def.label}
      >
        {def.options?.map(opt => (
          <option key={opt.value} value={opt.value} style={{ background: '#1e293b' }}>
            {opt.label}
          </option>
        ))}
      </select>
      <p className="text-[10px] text-slate-600 leading-tight">{def.description}</p>
    </div>
  );
}

export function ConstitutionEditor({ constitution, onSave, readOnly = false }: Props) {
  const [draft, setDraft] = useState<CivilizationConstitution>(() => ({ ...constitution }));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [jsonOpen, setJsonOpen] = useState(false);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [jsonDraft, setJsonDraft] = useState('');
  const isDirty = JSON.stringify(draft) !== JSON.stringify(constitution);

  const updateField = useCallback(<K extends keyof CivilizationConstitution>(
    key: K, value: CivilizationConstitution[K]
  ) => {
    setDraft(prev => ({ ...prev, [key]: value }));
    setSaved(false);
  }, []);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(draft);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setDraft({ ...constitution });
    setSaved(false);
  };

  const handleJsonApply = () => {
    try {
      const parsed = JSON.parse(jsonDraft) as CivilizationConstitution;
      setDraft(parsed);
      setJsonError(null);
      setJsonOpen(false);
    } catch (e) {
      setJsonError((e as Error).message);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-200">Governance Rules</h3>
          <p className="text-[10px] text-slate-500 mt-0.5">Controls society behaviour and resource limits</p>
        </div>
        {isDirty && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30">
            Unsaved
          </span>
        )}
      </div>

      {/* Form fields */}
      <div className="space-y-4">
        {FIELDS.map(def => {
          const val = draft[def.key];
          if (def.type === 'slider') {
            return (
              <SliderField
                key={String(def.key)}
                def={def}
                value={typeof val === 'number' ? val : 0}
                onChange={v => updateField(def.key, v as CivilizationConstitution[typeof def.key])}
                readOnly={readOnly}
              />
            );
          }
          if (def.type === 'toggle') {
            return (
              <ToggleField
                key={String(def.key)}
                def={def}
                value={Boolean(val)}
                onChange={v => updateField(def.key, v as CivilizationConstitution[typeof def.key])}
                readOnly={readOnly}
              />
            );
          }
          if (def.type === 'select') {
            return (
              <SelectField
                key={String(def.key)}
                def={def}
                value={String(val ?? '')}
                onChange={v => updateField(def.key, v as CivilizationConstitution[typeof def.key])}
                readOnly={readOnly}
              />
            );
          }
          return null;
        })}
      </div>

      {/* Raw JSON toggle */}
      <button
        onClick={() => {
          setJsonOpen(o => !o);
          if (!jsonOpen) setJsonDraft(JSON.stringify(draft, null, 2));
        }}
        className="w-full flex items-center gap-2 text-xs text-slate-500 hover:text-slate-300 transition-colors py-1"
      >
        {jsonOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        Raw JSON
      </button>

      {jsonOpen && (
        <div className="space-y-2">
          <textarea
            value={jsonDraft}
            onChange={e => { setJsonDraft(e.target.value); setJsonError(null); }}
            rows={10}
            className="
              w-full text-xs font-mono rounded-lg p-3 resize-none
              bg-black/30 border border-white/8 text-slate-300
              focus:outline-none focus:border-indigo-500/40
            "
            spellCheck={false}
          />
          {jsonError && (
            <div className="flex items-start gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg p-2">
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
              {jsonError}
            </div>
          )}
          {!readOnly && (
            <button
              onClick={handleJsonApply}
              className="text-xs px-3 py-1.5 rounded-lg bg-indigo-600/80 text-white hover:bg-indigo-500 transition-colors"
            >
              Apply JSON
            </button>
          )}
        </div>
      )}

      {/* Action buttons */}
      {!readOnly && (
        <div className="flex gap-2 pt-2 border-t border-white/5">
          <button
            onClick={() => void handleSave()}
            disabled={saving || !isDirty}
            className="
              flex items-center gap-1.5 flex-1 justify-center px-3 py-2 rounded-lg text-xs font-semibold
              bg-indigo-600 text-white hover:bg-indigo-500
              disabled:opacity-30 disabled:cursor-not-allowed
              transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-150 active:scale-95
            "
          >
            {saving
              ? <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              : saved
                ? <Check className="h-3.5 w-3.5" />
                : <Save className="h-3.5 w-3.5" />
            }
            {saving ? 'Saving…' : saved ? 'Saved!' : 'Save Constitution'}
          </button>
          <button
            onClick={handleReset}
            disabled={!isDirty || saving}
            title="Reset to last saved"
            className="
              flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs
              bg-white/5 border border-white/10 text-slate-400
              hover:bg-white/10 hover:text-slate-200
              disabled:opacity-30 disabled:cursor-not-allowed
              transition-[color,background-color,border-color,opacity,box-shadow,transform] duration-150
            "
          >
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
