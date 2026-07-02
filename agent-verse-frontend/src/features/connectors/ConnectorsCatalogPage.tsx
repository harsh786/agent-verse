import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2, Zap, Search, SlidersHorizontal } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { connectorsApi, type CatalogEntry } from '@/lib/api/client';

const CATEGORY_LABELS: Record<string, string> = {
  all: 'All',
  project_management: 'Project Mgmt',
  devtools: 'Dev Tools',
  communication: 'Communication',
  crm: 'CRM',
  finance: 'Finance',
  cloud: 'Cloud',
  database: 'Database',
  observability: 'Observability',
  productivity: 'Productivity',
  other: 'Other',
};

const CATEGORY_COLORS: Record<string, string> = {
  project_management: 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300',
  devtools: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  communication: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
  crm: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300',
  finance: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300',
  cloud: 'bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300',
  database: 'bg-slate-100 text-slate-700 dark:bg-slate-800/50 dark:text-slate-300',
  observability: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  productivity: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300',
  other: 'bg-gray-100 text-gray-600 dark:bg-gray-800/50 dark:text-gray-400',
};

const AUTH_COLORS: Record<string, string> = {
  bearer: 'bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300',
  api_key: 'bg-purple-50 text-purple-700 dark:bg-purple-950/40 dark:text-purple-300',
  basic: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
  oauth_ac: 'bg-green-50 text-green-700 dark:bg-green-950/40 dark:text-green-300',
  connection_string: 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300',
};

const AUTH_LABELS: Record<string, string> = {
  bearer: 'Bearer Token',
  api_key: 'API Key',
  basic: 'Basic Auth',
  oauth_ac: 'OAuth 2.0',
  connection_string: 'Connection URL',
};

const CONNECTOR_EMOJIS: Record<string, string> = {
  jira: '🎯', github: '🐙', slack: '💬', linear: '📐', hubspot: '🟠',
  stripe: '💳', datadog: '🐶', sentry: '🔴', salesforce: '☁️', aws: '🔶',
  gcp: '🔷', gitlab: '🦊', confluence: '🌊', asana: '🏃', notion: '📝',
  discord: '🎮', twilio: '📞', okta: '🔐', kubernetes: '⛵', terraform: '🏗️',
  postgresql: '🐘', mysql: '🐬', mongodb: '🍃', snowflake: '❄️', quickbooks: '📊',
  google_sheets: '📋', miro: '🎨', teams: '🟦', circleci: '🔄', pagerduty: '🚨',
};

function ConnectorCard({
  entry,
  onConfigure,
}: {
  entry: CatalogEntry;
  onConfigure: (entry: CatalogEntry) => void;
}) {
  const emoji = CONNECTOR_EMOJIS[entry.name] ?? '🔌';
  const categoryColor = CATEGORY_COLORS[entry.category] ?? CATEGORY_COLORS.other;
  const categoryLabel = CATEGORY_LABELS[entry.category] ?? entry.category;
  const authLabel = AUTH_LABELS[entry.auth_type] ?? entry.auth_type;
  const authColor = AUTH_COLORS[entry.auth_type] ?? AUTH_COLORS.api_key;

  return (
    <div
      className={`relative flex flex-col rounded-2xl border bg-card shadow-sm transition-shadow hover:shadow-md ${
        entry.is_configured
          ? 'border-emerald-200 dark:border-emerald-800/60'
          : 'border-border'
      }`}
    >
      {entry.is_configured && (
        <div className="absolute -top-2.5 right-3 flex items-center gap-1 rounded-full bg-emerald-500 px-2.5 py-0.5 text-[10px] font-semibold text-white shadow">
          <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
          Configured
        </div>
      )}

      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl bg-muted text-xl">
            {emoji}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
              <h3 className="font-semibold text-foreground">
                {entry.display_name || entry.name}
              </h3>
              {entry.has_builtin && (
                <span
                  title="Native optimised handler — faster and more reliable"
                  className="inline-flex items-center gap-0.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
                >
                  <Zap className="h-2.5 w-2.5" aria-hidden="true" />
                  Native
                </span>
              )}
            </div>
            <div className="mt-1 flex flex-wrap gap-1">
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${categoryColor}`}>
                {categoryLabel}
              </span>
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${authColor}`}>
                {authLabel}
              </span>
            </div>
          </div>
        </div>

        <p className="text-sm text-muted-foreground line-clamp-2 leading-relaxed">
          {entry.description}
        </p>

        {entry.auth_fields && entry.auth_fields.length > 0 && (
          <div className="rounded-lg border bg-muted/30 px-3 py-2">
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Required
            </p>
            <ul className="space-y-0.5">
              {entry.auth_fields.filter((f) => f.required).slice(0, 3).map((f) => (
                <li key={f.key} className="flex items-start gap-1.5 text-xs text-foreground/80">
                  <span className="mt-1.5 h-1 w-1 flex-shrink-0 rounded-full bg-muted-foreground/50" />
                  <span>
                    <span className="font-medium">{f.label}</span>
                    {f.hint && (
                      <span className="ml-1 text-muted-foreground">— {f.hint}</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="mt-auto border-t p-3">
        <button
          type="button"
          onClick={() => onConfigure(entry)}
          className={`w-full rounded-xl py-2 text-xs font-semibold transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 ${
            entry.is_configured
              ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-950/40 dark:text-emerald-300'
              : 'bg-primary text-primary-foreground'
          }`}
        >
          {entry.is_configured ? 'Reconfigure' : 'Configure'}
        </button>
      </div>
    </div>
  );
}

export function ConnectorsCatalogPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');

  const { data: catalog = [], isLoading, isError } = useQuery({
    queryKey: ['connectors-catalog'],
    queryFn: () => connectorsApi.getCatalog(),
    enabled: !!apiKey,
    staleTime: 60_000,
  });

  const categories = useMemo(() => {
    const cats = new Set(catalog.map((e) => e.category).filter(Boolean));
    return ['all', ...Array.from(cats).sort()];
  }, [catalog]);

  const filtered = useMemo(() => {
    let result = catalog;
    if (activeCategory !== 'all') {
      result = result.filter((e) => e.category === activeCategory);
    }
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (e) =>
          e.name.toLowerCase().includes(q) ||
          (e.display_name ?? '').toLowerCase().includes(q) ||
          e.description.toLowerCase().includes(q)
      );
    }
    return [...result].sort((a, b) => {
      if (a.is_configured !== b.is_configured) return a.is_configured ? -1 : 1;
      if (a.has_builtin !== b.has_builtin) return a.has_builtin ? -1 : 1;
      return (a.display_name || a.name).localeCompare(b.display_name || b.name);
    });
  }, [catalog, search, activeCategory]);

  const configuredCount = catalog.filter((e) => e.is_configured).length;

  const handleConfigure = (entry: CatalogEntry) => {
    navigate('/connectors', {
      state: {
        prefill: {
          connector_type: entry.connector_type ?? entry.name,
          name: entry.name,
          url: entry.default_url,
          auth_type: entry.auth_type,
          auth_fields: entry.auth_fields,
        },
      },
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">Connector Catalog</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {catalog.length} available connectors
            {configuredCount > 0 && (
              <span className="ml-2 font-medium text-emerald-600 dark:text-emerald-400">
                · {configuredCount} configured
              </span>
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={() => navigate('/connectors')}
          className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm font-medium text-foreground shadow-sm hover:bg-muted transition-colors"
        >
          My Connectors
        </button>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <input
            type="search"
            placeholder="Search connectors…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-lg border border-input bg-background pl-9 pr-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary"
            aria-label="Search connectors"
          />
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by category">
          {categories.map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setActiveCategory(cat)}
              aria-pressed={activeCategory === cat}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                activeCategory === cat
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-muted/80'
              }`}
            >
              {CATEGORY_LABELS[cat] ?? cat}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 9 }).map((_, i) => (
            <div key={i} className="h-52 animate-pulse rounded-2xl border bg-muted/40" />
          ))}
        </div>
      ) : isError ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-6 text-center text-sm text-red-700 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300">
          Failed to load catalog. Make sure the backend is running.
        </div>
      ) : filtered.length === 0 ? (
        <div className="py-16 text-center text-sm text-muted-foreground">
          <SlidersHorizontal className="mx-auto mb-3 h-8 w-8 opacity-30" />
          No connectors match your search.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((entry) => (
            <ConnectorCard key={entry.name} entry={entry} onConfigure={handleConfigure} />
          ))}
        </div>
      )}
    </div>
  );
}
