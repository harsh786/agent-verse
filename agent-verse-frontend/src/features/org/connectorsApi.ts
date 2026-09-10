/**
 * connectorsApi — real MCP connector marketplace + install flow.
 *
 * Talks to the backend Connectors API (app/api/connectors.py), which lives at
 * /connectors (NOT under /v1). This replaces the old mock install: connectors
 * are really registered, credentials are stored as vault secret refs, and the
 * /test endpoint validates them against the provider's live REST API.
 */
import { apiFetch } from '@/lib/api/client';

const BASE = '/connectors';

/** One credential input a connector needs, as declared by the catalog. */
export interface CatalogAuthField {
  key:         string;
  label:       string;
  placeholder: string;
  field_type:  string;   // 'password' | 'text' | 'url' | 'email' | …
  required:    boolean;
  hint?:       string;
}

/** A connector type available to install, with its credential spec. */
export interface CatalogConnector {
  name:              string;
  display_name:      string;
  description:       string;
  auth_type:         string;   // 'api_key' | 'bearer' | 'basic' | 'oauth_ac' | 'connection_string'
  default_url:       string;
  icon:              string;
  category:          string;
  auth_fields:       CatalogAuthField[];
  has_builtin:       boolean;
  builtin_server_id: string | null;
  is_configured:     boolean;
  connector_type:    string;
}

/** A connector this tenant has actually registered. */
export interface InstalledConnector {
  server_id:   string;
  name:        string;
  url:         string;
  auth_type:   string;
  auth_config: Record<string, unknown>;
  has_builtin?: boolean;
}

/** Result of validating a connector's credentials against its live API. */
export interface ConnectorTestResult {
  server_id?:  string;
  reachable?:  boolean;
  status?:     string;   // 'passed' | 'failed'
  latency_ms?: number;
  detail?:     string;   // success message (e.g. "Authenticated as @octocat")
  error?:      string;   // failure message
}

export const connectorsApi = {
  /** Every connector type, with per-tenant `is_configured` status. */
  catalog(): Promise<CatalogConnector[]> {
    return apiFetch<CatalogConnector[]>(`${BASE}/catalog`);
  },

  /** Connectors this tenant has registered (built-ins + user-added). */
  installed(): Promise<InstalledConnector[]> {
    return apiFetch<InstalledConnector[]>(BASE);
  },

  /** Register a connector with its credentials. Returns the created record. */
  register(body: {
    name: string;
    url: string;
    auth_type: string;
    auth_config: Record<string, string>;
    description?: string;
  }): Promise<InstalledConnector> {
    return apiFetch<InstalledConnector>(BASE, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  /** Validate a registered connector's credentials against its live API. */
  test(serverId: string): Promise<ConnectorTestResult> {
    return apiFetch<ConnectorTestResult>(`${BASE}/${encodeURIComponent(serverId)}/test`, {
      method: 'POST',
    });
  },

  /** Remove a connector. */
  remove(serverId: string): Promise<void> {
    return apiFetch<void>(`${BASE}/${encodeURIComponent(serverId)}`, { method: 'DELETE' });
  },

  /** Begin an OAuth popup flow — returns the provider authorize URL + CSRF state. */
  oauthStart(connectorName: string): Promise<{ auth_url: string; state: string }> {
    return apiFetch<{ auth_url: string; state: string }>(`${BASE}/oauth/start`, {
      method: 'POST',
      body: JSON.stringify({ connector_name: connectorName }),
    });
  },

  /** Complete an OAuth popup flow with the code the provider returned. */
  oauthCallback(body: { code: string; state: string; connector_name: string }): Promise<unknown> {
    return apiFetch<unknown>(`${BASE}/oauth/callback`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },
};

/** True when a test result indicates the credentials are valid. */
export function isTestPassed(r: ConnectorTestResult): boolean {
  return r.status === 'passed' || r.reachable === true;
}
