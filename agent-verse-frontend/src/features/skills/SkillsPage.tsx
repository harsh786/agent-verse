import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../../stores/auth';

const API = import.meta.env.VITE_API_URL || '';

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
}

interface FormState {
  name: string;
  description: string;
  trigger_hints: string;
  instructions: string;
  allowed_tools: string;
}

export default function SkillsPage() {
  const apiKey = useAuthStore(s => s.apiKey) || '';
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<FormState>({
    name: '',
    description: '',
    trigger_hints: '',
    instructions: '',
    allowed_tools: '',
  });

  const { data, isLoading } = useQuery<{ skills: Skill[] }>({
    queryKey: ['skills'],
    queryFn: async () => {
      const res = await fetch(`${API}/skills`, {
        headers: { 'X-API-Key': apiKey },
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    enabled: !!apiKey,
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API}/skills`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': apiKey,
        },
        body: JSON.stringify({
          name: form.name,
          description: form.description,
          trigger_hints: form.trigger_hints
            .split(',')
            .map(s => s.trim())
            .filter(Boolean),
          instructions: form.instructions,
          allowed_tools: form.allowed_tools
            .split(',')
            .map(s => s.trim())
            .filter(Boolean),
        }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] });
      setShowCreate(false);
      setForm({
        name: '',
        description: '',
        trigger_hints: '',
        instructions: '',
        allowed_tools: '',
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (skillId: string) => {
      await fetch(`${API}/skills/${skillId}`, {
        method: 'DELETE',
        headers: { 'X-API-Key': apiKey },
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['skills'] }),
  });

  const skills = data?.skills ?? [];
  const platformSkills = skills.filter(s => s.is_platform);
  const customSkills = skills.filter(s => !s.is_platform);

  const fieldConfigs = [
    {
      key: 'name' as keyof FormState,
      label: 'Name',
      placeholder: 'my-research-skill',
    },
    {
      key: 'description' as keyof FormState,
      label: 'Description',
      placeholder: 'Improves web research quality',
    },
    {
      key: 'trigger_hints' as keyof FormState,
      label: 'Trigger Hints (comma-separated)',
      placeholder: 'research, search, investigate',
    },
    {
      key: 'allowed_tools' as keyof FormState,
      label: 'Allowed Tools (comma-separated)',
      placeholder: 'web_search, document_reader',
    },
  ] as const;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Skills</h1>
          <p className="text-muted-foreground mt-1">
            Composable instruction packs that reduce tokens and improve focus
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
        >
          + Create Skill
        </button>
      </div>

      {showCreate && (
        <div className="rounded-xl border bg-card p-6 mb-6 space-y-3">
          <h2 className="font-semibold text-foreground">Create Custom Skill</h2>
          {fieldConfigs.map(({ key, label, placeholder }) => (
            <div key={key}>
              <label className="block text-xs font-medium text-muted-foreground mb-1">
                {label}
              </label>
              <input
                className="w-full border rounded px-3 py-1.5 text-sm bg-background text-foreground"
                placeholder={placeholder}
                value={form[key]}
                onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
              />
            </div>
          ))}
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1">
              Instructions
            </label>
            <textarea
              className="w-full border rounded px-3 py-1.5 text-sm bg-background text-foreground min-h-[80px]"
              placeholder="Always cross-reference at least 3 sources. Cite URLs for every claim..."
              value={form.instructions}
              onChange={e => setForm(f => ({ ...f, instructions: e.target.value }))}
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => createMutation.mutate()}
              disabled={!form.name || !form.instructions || createMutation.isPending}
              className="px-4 py-2 rounded bg-primary text-primary-foreground text-sm disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating…' : 'Create'}
            </button>
            <button
              onClick={() => setShowCreate(false)}
              className="px-4 py-2 rounded border text-sm text-foreground"
            >
              Cancel
            </button>
          </div>
          {createMutation.isError && (
            <p className="text-xs text-destructive">
              Failed to create skill. Please try again.
            </p>
          )}
        </div>
      )}

      {isLoading ? (
        <div className="text-sm text-muted-foreground">Loading skills…</div>
      ) : (
        <div className="space-y-6">
          <section>
            <h2 className="text-sm font-semibold text-muted-foreground mb-3">
              Platform Skills ({platformSkills.length})
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {platformSkills.map(skill => (
                <div key={skill.id} className="rounded-lg border bg-card p-4">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="font-medium text-sm text-foreground">{skill.name}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {skill.description || skill.instructions.slice(0, 80)}
                      </p>
                    </div>
                    <span className="text-xs px-2 py-0.5 rounded bg-primary/10 text-primary font-medium">
                      ~{skill.token_estimate} tokens
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1 mt-2">
                    {skill.trigger_hints.slice(0, 4).map(h => (
                      <span
                        key={h}
                        className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground"
                      >
                        {h}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>

          {customSkills.length > 0 && (
            <section>
              <h2 className="text-sm font-semibold text-muted-foreground mb-3">
                Custom Skills ({customSkills.length})
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {customSkills.map(skill => (
                  <div key={skill.id} className="rounded-lg border bg-card p-4">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="font-medium text-sm text-foreground">{skill.name}</p>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          {skill.description}
                        </p>
                      </div>
                      <button
                        onClick={() => deleteMutation.mutate(skill.id)}
                        disabled={deleteMutation.isPending}
                        className="text-xs text-destructive hover:underline disabled:opacity-50"
                      >
                        Delete
                      </button>
                    </div>
                    <div className="flex flex-wrap gap-1 mt-2">
                      {skill.trigger_hints.slice(0, 4).map(h => (
                        <span
                          key={h}
                          className="text-xs px-2 py-0.5 rounded-full bg-muted text-muted-foreground"
                        >
                          {h}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {skills.length === 0 && (
            <p className="text-sm text-muted-foreground">No skills found.</p>
          )}
        </div>
      )}
    </div>
  );
}
