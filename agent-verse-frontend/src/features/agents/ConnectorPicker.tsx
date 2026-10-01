/**
 * ConnectorPicker — multi-select of the tenant's registered connectors.
 *
 * Replaces the free-text "Connector IDs" box: each registered connector
 * INSTANCE is listed by its own name (a tenant may register several of one
 * type, e.g. two MongoDBs "orders-db" and "analytics-db"), with its type and
 * opaque server id as secondary labels, and the picker stores that instance's
 * `server_id` — exactly what the agent create/update API expects in
 * `connector_ids`. Selected ids that are no longer registered stay visible as
 * "missing" so they can be seen and removed instead of silently kept.
 */
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { connectorsApi } from '@/lib/api/client';
import { connectorLabel, connectorTypeLabel } from '@/lib/connectors';

const SEARCH_THRESHOLD = 6;

export function useRegisteredConnectors(enabled = true) {
  return useQuery({
    queryKey: ['connectors'],
    queryFn: () => connectorsApi.list(),
    enabled,
  });
}

export function ConnectorPicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (ids: string[]) => void;
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const { data, isLoading, error } = useRegisteredConnectors();
  const registered = useMemo(
    () =>
      [...(Array.isArray(data) ? data : [])].sort((a, b) =>
        connectorLabel(a).localeCompare(connectorLabel(b)),
      ),
    [data],
  );
  const registeredIds = useMemo(() => new Set(registered.map((c) => c.server_id)), [registered]);
  const selected = new Set(value);
  const missing = error ? [] : value.filter((id) => !registeredIds.has(id));

  const toggle = (id: string, checked: boolean) =>
    onChange(checked ? (selected.has(id) ? value : [...value, id]) : value.filter((v) => v !== id));

  const q = query.trim().toLowerCase();
  const visible = q
    ? registered.filter((c) =>
        [connectorLabel(c), c.server_id, connectorTypeLabel(c)].some((s) => s.toLowerCase().includes(q)),
      )
    : registered;

  if (isLoading) {
    return <p className="text-xs text-muted-foreground">Loading connectors…</p>;
  }

  if (error) {
    return (
      <div className="space-y-2">
        <p role="alert" className="text-xs text-red-500">
          Couldn't load connectors: {error instanceof Error ? error.message : String(error)}
        </p>
        {value.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {value.map((id) => (
              <span key={id} className="font-mono text-[11px] px-2 py-0.5 rounded bg-muted text-muted-foreground">
                {id}
              </span>
            ))}
          </div>
        )}
      </div>
    );
  }

  const emptyPrompt = registered.length === 0 && (
    <p className="text-xs text-muted-foreground">
      No connectors registered yet.{' '}
      <button
        type="button"
        onClick={() => navigate('/connectors/catalog')}
        className="text-primary hover:underline"
      >
        Add a connector
      </button>
    </p>
  );

  if (registered.length === 0 && missing.length === 0) return emptyPrompt;

  return (
    <div className="space-y-2">
      {emptyPrompt}
      {registered.length > SEARCH_THRESHOLD && (
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by name, type or server id"
          aria-label="Filter connectors"
          className="w-full px-3 py-1.5 border border-input rounded-lg text-xs bg-background focus:ring-2 focus:ring-primary outline-none"
        />
      )}
      <div className="max-h-56 overflow-y-auto rounded-lg border border-input bg-background divide-y divide-border">
        {visible.map((c) => {
          const label = connectorLabel(c);
          const typeLabel = connectorTypeLabel(c);
          return (
            <label
              key={c.server_id}
              className="flex items-start gap-2.5 px-3 py-2 text-sm cursor-pointer hover:bg-accent/50"
            >
              <input
                type="checkbox"
                checked={selected.has(c.server_id)}
                onChange={(e) => toggle(c.server_id, e.target.checked)}
                aria-label={label}
                className="mt-0.5 h-4 w-4 rounded border-input accent-[#00D4FF]"
              />
              <span className="flex-1 min-w-0">
                <span className="flex items-center gap-2">
                  <span className="truncate font-medium">{label}</span>
                  {typeLabel && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground shrink-0">
                      {typeLabel}
                    </span>
                  )}
                </span>
                {label !== c.server_id && (
                  <span className="block font-mono text-[11px] text-muted-foreground truncate">{c.server_id}</span>
                )}
              </span>
              {c.status && <span className="text-[10px] text-muted-foreground shrink-0">{c.status}</span>}
            </label>
          );
        })}
        {q && visible.length === 0 && (
          <p className="px-3 py-2 text-xs text-muted-foreground">No connector matches “{query}”.</p>
        )}
        {missing.map((id) => (
          <label
            key={id}
            className="flex items-center gap-2.5 px-3 py-2 text-sm cursor-pointer hover:bg-accent/50"
            title="No longer registered for this tenant — uncheck to remove it from the agent"
          >
            <input
              type="checkbox"
              checked
              onChange={() => toggle(id, false)}
              aria-label={id}
              className="h-4 w-4 rounded border-input accent-[#00D4FF]"
            />
            <span className="flex-1 min-w-0 font-mono text-[11px] truncate">{id}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 shrink-0">
              missing
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
