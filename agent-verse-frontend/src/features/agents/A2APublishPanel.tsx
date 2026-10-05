import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { agentsApi, type AgentResponse } from '@/lib/api/client';

const MAX_DESCRIPTION = 500;
const MAX_SKILLS = 20;
const MAX_SKILL_LEN = 80;

function parseSkills(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(',')) {
    const skill = raw.trim();
    if (skill && !out.includes(skill)) out.push(skill);
  }
  return out;
}

/**
 * D3 — publish this agent to the public A2A directory (/.well-known/agents).
 *
 * Off by default. Only the name, the description and skills written here, and the
 * endpoint are public — never the prompt, tools, model or connectors. The agent is
 * listed only while the tenant's A2A directory (Settings → Security) is on.
 */
export function A2APublishPanel({ agent }: { agent: AgentResponse }) {
  const qc = useQueryClient();
  const [isPublic, setIsPublic] = useState(Boolean(agent.a2a_public));
  const [description, setDescription] = useState(agent.a2a_description ?? '');
  const [skills, setSkills] = useState((agent.a2a_skills ?? []).join(', '));

  useEffect(() => {
    setIsPublic(Boolean(agent.a2a_public));
    setDescription(agent.a2a_description ?? '');
    setSkills((agent.a2a_skills ?? []).join(', '));
  }, [agent.agent_id, agent.a2a_public, agent.a2a_description, agent.a2a_skills]);

  const parsed = parseSkills(skills);
  const invalid =
    description.length > MAX_DESCRIPTION
      ? `Description is at most ${MAX_DESCRIPTION} characters.`
      : parsed.length > MAX_SKILLS
        ? `At most ${MAX_SKILLS} skills.`
        : parsed.some((s) => s.length > MAX_SKILL_LEN)
          ? `Each skill is at most ${MAX_SKILL_LEN} characters.`
          : null;

  const save = useMutation({
    mutationFn: () =>
      agentsApi.updateA2A(agent.agent_id, {
        a2a_public: isPublic,
        a2a_description: description.trim(),
        a2a_skills: parsed,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent', agent.agent_id] }),
  });

  return (
    <section aria-labelledby="a2a-publish-heading" className="bg-card border border-border rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 id="a2a-publish-heading" className="font-medium text-sm">Public A2A directory</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Listed only while your tenant&apos;s A2A directory is on. Only the name, this
            description and skills, and the endpoint are public.
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={isPublic}
          aria-label="List this agent in the public A2A directory"
          onClick={() => setIsPublic((v) => !v)}
          className={`relative inline-flex h-6 w-11 shrink-0 rounded-full transition-colors ${
            isPublic ? 'bg-primary' : 'bg-muted'
          }`}
        >
          <span
            className={`inline-block h-5 w-5 rounded-full bg-background shadow transform transition-transform mt-0.5 ${
              isPublic ? 'translate-x-5' : 'translate-x-0.5'
            }`}
          />
        </button>
      </div>
      <div>
        <label htmlFor="a2a-description" className="block text-xs font-medium mb-1">
          Public description
        </label>
        <textarea
          id="a2a-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none resize-none"
        />
      </div>
      <div>
        <label htmlFor="a2a-skills" className="block text-xs font-medium mb-1">
          Skills (comma separated)
        </label>
        <input
          id="a2a-skills"
          value={skills}
          onChange={(e) => setSkills(e.target.value)}
          className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none"
        />
      </div>
      {(invalid || save.error) && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {invalid ?? (save.error instanceof Error ? save.error.message : String(save.error))}
        </p>
      )}
      {save.isSuccess && !save.isPending && (
        <p role="status" className="text-xs text-muted-foreground">Saved.</p>
      )}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending || invalid !== null}
          className="px-4 py-2 bg-primary text-primary-foreground text-sm rounded-md hover:opacity-90 disabled:opacity-50"
        >
          {save.isPending ? 'Saving…' : 'Save directory settings'}
        </button>
      </div>
    </section>
  );
}
