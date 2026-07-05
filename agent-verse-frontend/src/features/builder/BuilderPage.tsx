import React, { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useAuthStore } from '../../stores/auth';

const API = import.meta.env.VITE_API_URL || '';

export default function BuilderPage() {
  const apiKey = useAuthStore(s => s.apiKey) || '';
  const [description, setDescription] = useState('');
  const [projectType, setProjectType] = useState('landing');
  const [framework, setFramework] = useState('react');
  const [project, setProject] = useState<any>(null);

  const buildMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API}/builder/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify({ description, project_type: projectType, framework }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    onSuccess: (data) => setProject(data),
  });

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-foreground">AI Builder</h1>
        <p className="text-muted-foreground mt-1">
          Describe what you want to build and let AI generate it for you.
        </p>
      </div>

      {!project ? (
        <div className="rounded-xl border bg-card p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-foreground mb-1">
              What do you want to build?
            </label>
            <textarea
              className="w-full rounded-lg border bg-background text-foreground p-3 text-sm min-h-[100px] focus:outline-none focus:ring-2 focus:ring-primary/30"
              placeholder="A landing page for a SaaS product with hero, features, pricing, and CTA..."
              value={description}
              onChange={e => setDescription(e.target.value)}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Project Type</label>
              <select
                className="w-full rounded-lg border bg-background text-foreground p-2 text-sm"
                value={projectType}
                onChange={e => setProjectType(e.target.value)}
              >
                <option value="landing">Landing Page</option>
                <option value="dashboard">Dashboard</option>
                <option value="saas">SaaS App</option>
                <option value="portfolio">Portfolio</option>
                <option value="ecommerce">E-commerce</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">Framework</label>
              <select
                className="w-full rounded-lg border bg-background text-foreground p-2 text-sm"
                value={framework}
                onChange={e => setFramework(e.target.value)}
              >
                <option value="react">React + Vite</option>
                <option value="vanilla">Vanilla HTML/CSS/JS</option>
                <option value="vue">Vue 3</option>
              </select>
            </div>
          </div>

          <button
            onClick={() => buildMutation.mutate()}
            disabled={!description.trim() || buildMutation.isPending}
            className="w-full py-3 rounded-lg bg-primary text-primary-foreground font-medium disabled:opacity-50 hover:opacity-90 transition-opacity"
          >
            {buildMutation.isPending ? 'Building\u2026' : '\u2728 Build with AI'}
          </button>
          {buildMutation.error && (
            <p className="text-sm text-destructive">{String(buildMutation.error)}</p>
          )}
        </div>
      ) : (
        <div className="rounded-xl border bg-card p-6 space-y-4">
          <div className="flex items-center gap-3">
            <div className="h-3 w-3 rounded-full bg-amber-400 animate-pulse" />
            <h2 className="font-semibold text-foreground">Building your {projectType}\u2026</h2>
          </div>
          <p className="text-sm text-muted-foreground">{project.description}</p>
          <div className="bg-muted rounded-lg p-4 text-xs font-mono text-muted-foreground">
            Goal ID: {project.goal_id || 'Starting\u2026'}<br/>
            Project ID: {project.project_id}<br/>
            Status: {project.status}
          </div>
          {project.goal_id && (
            <a
              href={`/goals/${project.goal_id}`}
              className="text-sm text-primary hover:underline"
            >
              View build progress &rarr;
            </a>
          )}
          <button
            onClick={() => setProject(null)}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            &larr; Start over
          </button>
        </div>
      )}
    </div>
  );
}
