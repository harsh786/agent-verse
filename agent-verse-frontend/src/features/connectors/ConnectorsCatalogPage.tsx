import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/auth';

const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

interface CatalogEntry {
  name: string;
  description: string;
  auth_type: string;
  default_url: string;
  category?: string;
}

async function fetchCatalog(apiKey: string): Promise<CatalogEntry[]> {
  const res = await fetch(`${API_BASE}/connectors/catalog`, {
    headers: { 'X-API-Key': apiKey },
  });
  if (!res.ok) throw new Error(`Failed to fetch catalog: ${res.statusText}`);
  return res.json();
}

const AUTH_COLORS: Record<string, string> = {
  bearer: 'bg-blue-100 text-blue-800',
  api_key: 'bg-purple-100 text-purple-800',
  basic: 'bg-gray-100 text-gray-800',
  oauth_ac: 'bg-green-100 text-green-800',
  pkce: 'bg-green-100 text-green-800',
  oauth_cc: 'bg-teal-100 text-teal-800',
};

export function ConnectorsCatalogPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const navigate = useNavigate();

  const { data: catalog = [], isLoading, error } = useQuery({
    queryKey: ['catalog'],
    queryFn: () => fetchCatalog(apiKey),
    enabled: !!apiKey,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Connector Catalog</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Browse available MCP connectors and register them for your tenant
        </p>
      </div>

      {isLoading ? (
        <div className="py-10 text-center text-sm text-muted-foreground">
          Loading catalog…
        </div>
      ) : error ? (
        <div className="py-10 text-center text-sm text-red-500">
          Failed to load catalog.
        </div>
      ) : catalog.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted-foreground">
          No catalog entries found.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {catalog.map((c) => (
            <div
              key={c.name}
              className="bg-card border border-border rounded-xl p-4 hover:shadow-md transition-shadow flex flex-col"
            >
              <div className="flex justify-between items-start mb-2">
                <h3 className="font-semibold capitalize">{c.name}</h3>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    AUTH_COLORS[c.auth_type] ?? 'bg-gray-100 text-gray-800'
                  }`}
                >
                  {c.auth_type}
                </span>
              </div>
              <p className="text-sm text-muted-foreground flex-1">{c.description}</p>
              <p className="text-xs text-muted-foreground mt-3 mb-4 font-mono truncate">
                {c.default_url}
              </p>
              <button
                onClick={() => navigate('/connectors/registered')}
                className="w-full bg-primary text-primary-foreground text-xs font-medium py-1.5 rounded-lg hover:opacity-90 transition-opacity"
              >
                Register
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
