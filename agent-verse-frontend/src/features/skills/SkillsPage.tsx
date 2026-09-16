import { useState, useRef } from 'react';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Pencil, Play, Trash2, Download, Upload, Search, X as XIcon, Wrench } from 'lucide-react';
import { useAuthStore, getAuthHeader } from '../../stores/auth';
import { API_BASE } from '@/lib/api/client';
import { toast } from '@/stores/toast';

interface Skill {
  id: string;
  name: string;
  description: string;
  trigger_hints: string[];
  instructions: string;
  allowed_tools: string[];
  token_estimate: number;
  visibility: string;
  is_platform: boolean;
  enabled?: boolean;
  use_count?: number;
}

interface FormState {
  name: string;
  description: string;
  trigger_hints: string;
  instructions: string;
  allowed_tools: string;
}

const EMPTY_FORM: FormState = {
  name: '',
  description: '',
  trigger_hints: '',
  instructions: '',
  allowed_tools: '',
};

function skillToForm(s: Skill): FormState {
  return {
    name: s.name,
    description: s.description ?? '',
    trigger_hints: s.trigger_hints.join(', '),
    instructions: s.instructions,
    allowed_tools: s.allowed_tools.join(', '),
  };
}

function formToBody(f: FormState) {
  return {
    name: f.name,
    description: f.description,
    trigger_hints: f.trigger_hints.split(',').map(s => s.trim()).filter(Boolean),
    instructions: f.instructions,
    allowed_tools: f.allowed_tools.split(',').map(s => s.trim()).filter(Boolean),
  };
}

// ── Standalone skill card (defined outside main component to avoid re-creation) ─

interface SkillCardProps {
  skill: Skill;
  isCustom: boolean;
  isDeleting: boolean;
  isToggling: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onTest: () => void;
  onDelete: () => void;
}

function SkillCard({
  skill,
  isCustom,
  isDeleting,
  isToggling,
  onToggle,
  onEdit,
  onTest,
  onDelete,
}: SkillCardProps) {
  const isEnabled = skill.enabled !== false;

  return (
    <div
      className={`rounded-lg border bg-card p-4 flex flex-col gap-3 transition-opacity ${
        !isEnabled ? 'opacity-60' : ''
      }`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="font-medium text-sm text-foreground">{skill.name}</p>
            <span
              className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                isEnabled
                  ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                  : 'bg-muted text-muted-foreground'
              }`}
            >
              {isEnabled ? 'Active' : 'Inactive'}
            </span>
            {skill.is_platform && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium">
                Platform
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
            {skill.description || skill.instructions.slice(0, 80)}
          </p>
        </div>
        <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono flex-shrink-0">
          ~{skill.token_estimate} tokens
        </span>
      </div>

      {/* Trigger hint chips */}
      <div className="flex flex-wrap gap-1">
        {skill.trigger_hints.slice(0, 3).map(h => (
          <span
            key={h}
            className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground"
          >
            {h}
          </span>
        ))}
        {skill.trigger_hints.length > 3 && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
            +{skill.trigger_hints.length - 3}
          </span>
        )}
      </div>

      {/* Meta row */}
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <Wrench className="h-3 w-3" />
          {skill.allowed_tools.length} tool{skill.allowed_tools.length !== 1 ? 's' : ''}
        </span>
        {skill.use_count != null && (
          <span>Used {skill.use_count}×</span>
        )}
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-2 pt-1 border-t border-border">
        {isCustom && (
          <button
            onClick={onToggle}
            disabled={isToggling}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
            title={isEnabled ? 'Disable skill' : 'Enable skill'}
          >
            {isEnabled ? (
              <span className="text-green-600">● On</span>
            ) : (
              <span>○ Off</span>
            )}
          </button>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={onTest}
            className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted rounded transition-colors"
          >
            <Play className="h-3 w-3" /> Test
          </button>
          {isCustom && (
            <>
              <button
                onClick={onEdit}
                className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground hover:bg-muted rounded transition-colors"
              >
                <Pencil className="h-3 w-3" /> Edit
              </button>
              <button
                onClick={onDelete}
                disabled={isDeleting}
                className="flex items-center gap-1 px-2 py-1 text-xs text-destructive hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950/20 rounded transition-colors disabled:opacity-50"
              >
                <Trash2 className="h-3 w-3" /> Delete
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Form fields config ────────────────────────────────────────────────────────

const FIELD_CONFIGS = [
  { key: 'name' as keyof FormState, label: 'Name', placeholder: 'my-research-skill' },
  { key: 'description' as keyof FormState, label: 'Description', placeholder: 'Improves web research quality' },
  { key: 'trigger_hints' as keyof FormState, label: 'Trigger Hints (comma-separated)', placeholder: 'research, search, investigate' },
  { key: 'allowed_tools' as keyof FormState, label: 'Allowed Tools (comma-separated)', placeholder: 'web_search, document_reader' },
] as const;

// ── Main page ─────────────────────────────────────────────────────────────────

export default function SkillsPage() {
  const apiKey = useAuthStore(s => s.apiKey) || '';
  const qc = useQueryClient();

  // Create form
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  // Edit modal
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [editForm, setEditForm] = useState<FormState>(EMPTY_FORM);

  // Test modal
  const [testingSkill, setTestingSkill] = useState<Skill | null>(null);
  const [testInput, setTestInput] = useState('');
  const [testResult, setTestResult] = useState<string | null>(null);

  // Search
  const [search, setSearch] = useState('');

  // Tracking per-skill pending states
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  // Import file ref
  const importRef = useRef<HTMLInputElement>(null);

  const headers = getAuthHeader();

  // ── Queries & mutations ──────────────────────────────────────────────────────

  const { data, isLoading } = useQuery<{ skills: Skill[] }>({
    queryKey: ['skills'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/skills`, { headers });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/skills`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify(formToBody(form)),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      toast({ kind: 'success', message: 'Skill created' });
      qc.invalidateQueries({ queryKey: ['skills'] });
      setShowCreate(false);
      setForm(EMPTY_FORM);
    },
    onError: (e) => toast({ kind: 'error', message: `Failed to create skill: ${String(e)}` }),
  });

  const updateMutation = useMutation({
    mutationFn: async (skill: Skill) => {
      const res = await fetch(`${API_BASE}/skills/${skill.id}`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...skill, ...formToBody(editForm) }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      toast({ kind: 'success', message: 'Skill updated' });
      setEditingSkill(null);
      qc.invalidateQueries({ queryKey: ['skills'] });
    },
    onError: (e) => toast({ kind: 'error', message: `Failed to update skill: ${String(e)}` }),
  });

  const deleteMutation = useMutation({
    mutationFn: async (skillId: string) => {
      setDeletingId(skillId);
      const res = await fetch(`${API_BASE}/skills/${skillId}`, { method: 'DELETE', headers });
      if (!res.ok) throw new Error(`${res.status}`);
    },
    onSuccess: () => {
      setDeletingId(null);
      toast({ kind: 'success', message: 'Skill deleted' });
      qc.invalidateQueries({ queryKey: ['skills'] });
    },
    onError: (e) => {
      setDeletingId(null);
      toast({ kind: 'error', message: `Failed to delete skill: ${String(e)}` });
    },
  });

  const toggleMutation = useMutation({
    mutationFn: async ({ id, enabled }: { id: string; enabled: boolean }) => {
      setTogglingId(id);
      const res = await fetch(`${API_BASE}/skills/${id}`, {
        method: 'PATCH',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      setTogglingId(null);
      qc.invalidateQueries({ queryKey: ['skills'] });
    },
    onError: (e) => {
      setTogglingId(null);
      toast({ kind: 'error', message: `Toggle failed: ${String(e)}` });
    },
  });

  const testMutation = useMutation({
    mutationFn: async ({ id, input }: { id: string; input: string }) => {
      const res = await fetch(`${API_BASE}/skills/${id}/test`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ input }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    onSuccess: (data) => setTestResult(JSON.stringify(data, null, 2)),
    onError: (e) => setTestResult(`Error: ${String(e)}`),
  });

  // ── Derived data ─────────────────────────────────────────────────────────────

  const skills = data?.skills ?? [];
  const platformSkills = skills.filter(s => s.is_platform);
  const customSkills = skills.filter(s => !s.is_platform);

  const matchesSearch = (s: Skill) =>
    !search ||
    s.name.toLowerCase().includes(search.toLowerCase()) ||
    (s.description ?? '').toLowerCase().includes(search.toLowerCase());

  const filteredPlatform = platformSkills.filter(matchesSearch);
  const filteredCustom = customSkills.filter(matchesSearch);

  // ── Export / Import ───────────────────────────────────────────────────────────

  const exportSkills = () => {
    const blob = new Blob([JSON.stringify(skills, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'skills-export.json';
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async (ev) => {
      try {
        const imported = JSON.parse(ev.target?.result as string) as Skill[];
        const toImport = Array.isArray(imported) ? imported.filter(s => !s.is_platform) : [];
        for (const skill of toImport) {
          await fetch(`${API_BASE}/skills`, {
            method: 'POST',
            headers: { ...headers, 'Content-Type': 'application/json' },
            body: JSON.stringify(formToBody(skillToForm(skill))),
          });
        }
        toast({ kind: 'success', message: `Imported ${toImport.length} skill(s)` });
        qc.invalidateQueries({ queryKey: ['skills'] });
      } catch (err) {
        toast({ kind: 'error', message: `Import failed: ${String(err)}` });
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  // ── Handlers ──────────────────────────────────────────────────────────────────

  const openEdit = (skill: Skill) => {
    setEditingSkill(skill);
    setEditForm(skillToForm(skill));
  };

  const openTest = (skill: Skill) => {
    setTestingSkill(skill);
    setTestInput('');
    setTestResult(null);
  };

  // ── Render ────────────────────────────────────────────────────────────────────

  return (
    <JARVISPageShell>
    <div className="p-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Skills</h1>
          <p className="text-muted-foreground mt-1">
            Composable instruction packs that reduce tokens and improve focus
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={exportSkills}
            disabled={skills.length === 0}
            className="flex items-center gap-1.5 px-3 py-2 text-xs border border-input rounded-lg hover:bg-muted/50 disabled:opacity-50 transition-colors"
          >
            <Download className="h-3.5 w-3.5" /> Export
          </button>
          <button
            onClick={() => importRef.current?.click()}
            className="flex items-center gap-1.5 px-3 py-2 text-xs border border-input rounded-lg hover:bg-muted/50 transition-colors"
          >
            <Upload className="h-3.5 w-3.5" /> Import
          </button>
          <input ref={importRef} type="file" accept=".json" className="hidden" onChange={handleImport} />
          <button
            onClick={() => setShowCreate(v => !v)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
          >
            + Create Skill
          </button>
        </div>
      </div>

      {/* Search bar */}
      <div className="flex items-center gap-2 bg-muted/40 border border-border rounded-lg px-3 py-2 mb-6">
        <Search className="h-4 w-4 text-muted-foreground flex-shrink-0" />
        <input
          type="search"
          placeholder="Search skills by name or description…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="bg-transparent text-sm outline-none flex-1 text-foreground placeholder:text-muted-foreground"
        />
        {search && (
          <button onClick={() => setSearch('')} className="text-muted-foreground hover:text-foreground">
            <XIcon className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* Create form */}
      {showCreate && (
        <div className="rounded-xl border bg-card p-6 mb-6 space-y-3">
          <h2 className="font-semibold text-foreground">Create Custom Skill</h2>
          {FIELD_CONFIGS.map(({ key, label, placeholder }) => (
            <div key={key}>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{label}</label>
              <input
                className="w-full border border-input rounded px-3 py-1.5 text-sm bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                placeholder={placeholder}
                value={form[key]}
                onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
              />
            </div>
          ))}
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1">Instructions</label>
            <textarea
              className="w-full border border-input rounded px-3 py-1.5 text-sm bg-background text-foreground min-h-[80px] focus:outline-none focus:ring-2 focus:ring-primary resize-none"
              placeholder="Always cross-reference at least 3 sources. Cite URLs for every claim..."
              value={form.instructions}
              onChange={e => setForm(f => ({ ...f, instructions: e.target.value }))}
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => createMutation.mutate()}
              disabled={!form.name || !form.instructions || createMutation.isPending}
              className="px-4 py-2 rounded bg-primary text-primary-foreground text-sm disabled:opacity-50 hover:opacity-90"
            >
              {createMutation.isPending ? 'Creating…' : 'Create'}
            </button>
            <button
              onClick={() => { setShowCreate(false); setForm(EMPTY_FORM); }}
              className="px-4 py-2 rounded border border-input text-sm text-foreground hover:bg-muted"
            >
              Cancel
            </button>
          </div>
          {createMutation.isError && (
            <p className="text-xs text-destructive">Failed to create skill. Please try again.</p>
          )}
        </div>
      )}

      {/* Edit modal */}
      {editingSkill && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-card border border-border rounded-xl p-6 w-full max-w-lg space-y-3 shadow-xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-foreground">Edit Skill</h2>
              <button onClick={() => setEditingSkill(null)} className="text-muted-foreground hover:text-foreground">
                <XIcon className="h-4 w-4" />
              </button>
            </div>
            {FIELD_CONFIGS.map(({ key, label, placeholder }) => (
              <div key={key}>
                <label className="block text-xs font-medium text-muted-foreground mb-1">{label}</label>
                <input
                  className="w-full border border-input rounded px-3 py-1.5 text-sm bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                  placeholder={placeholder}
                  value={editForm[key]}
                  onChange={e => setEditForm(f => ({ ...f, [key]: e.target.value }))}
                />
              </div>
            ))}
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">Instructions</label>
              <textarea
                className="w-full border border-input rounded px-3 py-1.5 text-sm bg-background text-foreground min-h-[100px] focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                value={editForm.instructions}
                onChange={e => setEditForm(f => ({ ...f, instructions: e.target.value }))}
              />
            </div>
            <div className="flex gap-2 pt-2">
              <button
                onClick={() => updateMutation.mutate(editingSkill)}
                disabled={!editForm.name || updateMutation.isPending}
                className="px-4 py-2 rounded bg-primary text-primary-foreground text-sm disabled:opacity-50 hover:opacity-90"
              >
                {updateMutation.isPending ? 'Saving…' : 'Save Changes'}
              </button>
              <button
                onClick={() => setEditingSkill(null)}
                className="px-4 py-2 rounded border border-input text-sm text-foreground hover:bg-muted"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Test modal */}
      {testingSkill && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-card border border-border rounded-xl p-6 w-full max-w-lg space-y-3 shadow-xl">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold text-foreground">Test: {testingSkill.name}</h2>
              <button onClick={() => setTestingSkill(null)} className="text-muted-foreground hover:text-foreground">
                <XIcon className="h-4 w-4" />
              </button>
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">Test Input</label>
              <textarea
                className="w-full border border-input rounded px-3 py-1.5 text-sm bg-background text-foreground min-h-[80px] focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                placeholder="Enter test input for this skill…"
                value={testInput}
                onChange={e => setTestInput(e.target.value)}
              />
            </div>
            <button
              onClick={() => testMutation.mutate({ id: testingSkill.id, input: testInput })}
              disabled={!testInput.trim() || testMutation.isPending}
              className="flex items-center gap-1.5 px-4 py-2 rounded bg-primary text-primary-foreground text-sm disabled:opacity-50 hover:opacity-90"
            >
              <Play className="h-3.5 w-3.5" />
              {testMutation.isPending ? 'Running…' : 'Run Test'}
            </button>
            {testResult && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">Result</p>
                <pre className="text-xs bg-muted rounded p-3 overflow-auto max-h-48 whitespace-pre-wrap text-foreground border border-border">
                  {testResult}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Skills content */}
      {isLoading ? (
        <div className="text-sm text-muted-foreground">Loading skills…</div>
      ) : (
        <div className="space-y-8">
          {/* Platform skills */}
          <section>
            <h2 className="text-sm font-semibold text-muted-foreground mb-3">
              Platform Skills (
              {filteredPlatform.length}
              {search && platformSkills.length !== filteredPlatform.length
                ? ` of ${platformSkills.length}`
                : ''}
              )
            </h2>
            {filteredPlatform.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {search ? 'No platform skills match your search.' : 'No platform skills available.'}
              </p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {filteredPlatform.map(skill => (
                  <SkillCard
                    key={skill.id}
                    skill={skill}
                    isCustom={false}
                    isDeleting={false}
                    isToggling={false}
                    onToggle={() => {}}
                    onEdit={() => openEdit(skill)}
                    onTest={() => openTest(skill)}
                    onDelete={() => {}}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Custom skills */}
          <section>
            <h2 className="text-sm font-semibold text-muted-foreground mb-3">
              Custom Skills (
              {filteredCustom.length}
              {search && customSkills.length !== filteredCustom.length
                ? ` of ${customSkills.length}`
                : ''}
              )
            </h2>
            {filteredCustom.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {customSkills.length === 0
                  ? 'No custom skills yet. Create one to get started.'
                  : 'No custom skills match your search.'}
              </p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {filteredCustom.map(skill => (
                  <SkillCard
                    key={skill.id}
                    skill={skill}
                    isCustom={true}
                    isDeleting={deletingId === skill.id}
                    isToggling={togglingId === skill.id}
                    onToggle={() => toggleMutation.mutate({ id: skill.id, enabled: skill.enabled === false })}
                    onEdit={() => openEdit(skill)}
                    onTest={() => openTest(skill)}
                    onDelete={() => deleteMutation.mutate(skill.id)}
                  />
                ))}
              </div>
            )}
          </section>

          {skills.length === 0 && (
            <p className="text-sm text-muted-foreground">No skills found.</p>
          )}
        </div>
      )}
    </div>
    </JARVISPageShell>
  );
}
