import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ShoppingBag, Plug } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

interface MarketplaceTemplate {
  template_id: string;
  name: string;
  domain: string;
  description: string;
  connectors?: string[];
  autonomy_mode?: string;
  author?: string;
}

interface DeployResult {
  agent_id: string;
  name?: string;
}

const DOMAINS = ['all', 'software', 'devops', 'testing', 'hr', 'sales', 'support'];

const DOMAIN_COLORS: Record<string, string> = {
  software: 'bg-blue-100 text-blue-800',
  devops: 'bg-purple-100 text-purple-800',
  testing: 'bg-yellow-100 text-yellow-800',
  hr: 'bg-pink-100 text-pink-800',
  sales: 'bg-green-100 text-green-800',
  support: 'bg-orange-100 text-orange-800',
};

async function fetchTemplates(apiKey: string): Promise<MarketplaceTemplate[]> {
  const res = await fetch(`${API_BASE}/marketplace/browse`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function deployTemplate(
  apiKey: string,
  templateId: string
): Promise<DeployResult> {
  const res = await fetch(`${API_BASE}/marketplace/${templateId}/deploy`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function PublishSection({ apiKey }: { apiKey: string }) {
  const [form, setForm] = useState({ name: '', domain: 'software', description: '', connectors: '' });
  const [published, setPublished] = useState<{ template_id: string; name: string } | null>(null);
  const qc = useQueryClient();

  const mutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/marketplace/publish`, {
        method: 'POST',
        headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          connectors: form.connectors ? form.connectors.split(',').map(s => s.trim()) : [],
        }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      return res.json();
    },
    onSuccess: (data) => {
      setPublished(data);
      qc.invalidateQueries({ queryKey: ['marketplace'] });
    },
  });

  if (published) {
    return (
      <div className="bg-green-50 border border-green-200 rounded-xl p-4 text-sm text-green-800">
        Published <strong>{published.name}</strong> as{' '}
        <code className="font-mono">{published.template_id}</code>
      </div>
    );
  }

  return (
    <div className="bg-card border border-border rounded-xl p-5 space-y-3">
      <h2 className="font-semibold text-sm">Publish Your Agent</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <input value={form.name} onChange={e => setForm(f => ({...f, name: e.target.value}))}
          placeholder="Template name" className="border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary" />
        <input value={form.domain} onChange={e => setForm(f => ({...f, domain: e.target.value}))}
          placeholder="Domain (e.g. software)" className="border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary" />
      </div>
      <textarea value={form.description} onChange={e => setForm(f => ({...f, description: e.target.value}))}
        placeholder="Describe what this agent does..." rows={2}
        className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary resize-none" />
      <input value={form.connectors} onChange={e => setForm(f => ({...f, connectors: e.target.value}))}
        placeholder="Connectors (comma-separated: jira, github)" className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background outline-none focus:ring-2 focus:ring-primary" />
      {mutation.isError && <p className="text-xs text-red-600">{String(mutation.error)}</p>}
      <button onClick={() => mutation.mutate()} disabled={!form.name || !form.description || mutation.isPending}
        className="bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50">
        {mutation.isPending ? 'Publishing…' : 'Publish to Marketplace'}
      </button>
    </div>
  );
}

export function MarketplacePage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const [domain, setDomain] = useState('all');
  const [deployResults, setDeployResults] = useState<Record<string, DeployResult>>({});

  const { data: templates = [], isLoading, error } = useQuery({
    queryKey: ['marketplace'],
    queryFn: () => fetchTemplates(apiKey),
    enabled: !!apiKey,
  });

  const deployMutation = useMutation({
    mutationFn: (id: string) => deployTemplate(apiKey, id),
    onSuccess: (data, id) =>
      setDeployResults((prev) => ({ ...prev, [id]: data })),
  });

  const filtered =
    domain === 'all'
      ? templates
      : templates.filter((t) => t.domain === domain);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Marketplace</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Deploy pre-built agent templates for common workflows
        </p>
      </div>

      <PublishSection apiKey={apiKey} />

      {/* Domain filter */}
      <div className="flex gap-2 flex-wrap">
        {DOMAINS.map((d) => (
          <button
            key={d}
            onClick={() => setDomain(d)}
            className={`px-3 py-1 text-xs rounded-full border transition-colors capitalize ${
              domain === d
                ? 'bg-primary text-primary-foreground border-primary'
                : 'border-border hover:bg-accent'
            }`}
          >
            {d}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="py-10 text-center text-sm text-muted-foreground">
          Loading marketplace…
        </div>
      ) : error ? (
        <div className="py-10 text-center text-sm text-red-500">
          Failed to load marketplace.
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">
          No templates found{domain !== 'all' ? ` in domain "${domain}"` : ''}.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((t) => {
            const deployed = deployResults[t.template_id];
            return (
              <div
                key={t.template_id}
                className="bg-card border border-border rounded-xl p-5 flex flex-col gap-3 hover:shadow-md transition-shadow"
              >
                <div className="flex justify-between items-start">
                  <div className="flex items-center gap-2">
                    <div className="p-1.5 bg-primary/10 rounded-lg">
                      <ShoppingBag className="h-4 w-4 text-primary" />
                    </div>
                    <h3 className="font-semibold text-sm leading-snug">{t.name}</h3>
                  </div>
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full font-medium capitalize ${
                      DOMAIN_COLORS[t.domain] ?? 'bg-gray-100 text-gray-800'
                    }`}
                  >
                    {t.domain}
                  </span>
                </div>

                <p className="text-sm text-muted-foreground flex-1 leading-relaxed">
                  {t.description}
                </p>

                {t.connectors && t.connectors.length > 0 && (
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <Plug className="h-3 w-3 text-muted-foreground" />
                    {t.connectors.map((c) => (
                      <span
                        key={c}
                        className="text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded"
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                )}

                {deployed ? (
                  <div className="px-3 py-2 bg-green-50 border border-green-200 rounded-lg text-xs text-green-800">
                    Deployed as <span className="font-mono">{deployed.agent_id}</span>
                    {deployed.name && ` — ${deployed.name}`}
                  </div>
                ) : (
                  <button
                    onClick={() => deployMutation.mutate(t.template_id)}
                    disabled={deployMutation.isPending}
                    className="w-full bg-primary text-primary-foreground py-2 rounded-lg text-sm hover:opacity-90 disabled:opacity-50 transition-opacity"
                  >
                    {deployMutation.isPending ? 'Deploying…' : 'Deploy'}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
