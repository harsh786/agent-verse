/**
 * Connector (connection) naming helpers — mirrors the backend contract:
 *
 * - a tenant may hold several connections of one built-in type; each has an
 *   opaque `server_id` (e.g. `builtin-mongodb:orders-db`) and a unique
 *   `display_name`;
 * - `builtin_type` is the canonical built-in id (`builtin-mongodb`) and
 *   `builtin_type_name` its human name (`MongoDB`);
 * - POST /connectors takes the built-in type as `type`;
 * - a tool addressed to one specific connection is `<connection_slug>__<tool>`
 *   (app/mcp/tool_naming.py), e.g. `orders_db__mongodb_find`.
 */
import type { ConnectorResponse } from '@/lib/api/client';

type Named = Pick<ConnectorResponse, 'server_id' | 'name' | 'display_name'>;

/** The connection's display name (falls back to name, then the opaque id). */
export function connectorLabel(c: Named): string {
  return c.display_name?.trim() || c.name?.trim() || c.server_id;
}

/** Human type label ("MongoDB") of a connection, '' for a plain remote MCP server. */
export function connectorTypeLabel(
  c: Pick<ConnectorResponse, 'builtin_type' | 'builtin_type_name'>,
): string {
  if (c.builtin_type_name?.trim()) return c.builtin_type_name.trim();
  return c.builtin_type ? c.builtin_type.replace(/^builtin-/, '').split(':')[0] : '';
}

/** Normalised type key — 'builtin-google-sheets', 'Google Sheets', 'google_sheets' → 'googlesheets'. */
export function connectorTypeKey(value: string | null | undefined): string {
  let text = (value ?? '').trim().toLowerCase();
  if (text.startsWith('builtin-')) text = text.split(':', 1)[0].slice('builtin-'.length);
  return text.replace(/[^a-z0-9]+/g, '');
}

/** Function-name-safe slug of a connection's display name (tool_naming.connection_slug). */
export function connectionSlug(name: string): string {
  const slug = (name ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return slug || 'connection';
}

const SEP = '__';
const MAX = 64;

/** `<connection_slug>__<tool>` (tool_naming.qualified_tool_name), <= 64 chars. */
export function qualifiedToolName(connectionName: string, toolName: string): string {
  const room = Math.max(1, MAX - SEP.length - toolName.length);
  const prefix = connectionSlug(connectionName).slice(0, room).replace(/_+$/, '') || 'c';
  return `${prefix}${SEP}${toolName}`;
}

// ── Connection URIs (DSNs) — secrets, never links ─────────────────────────────

const DSN_SCHEME_RE = /^(mongodb(\+srv)?|postgres(ql)?|mysql|rediss?):\/\//i;

/** True for a database connection URI (mongodb://, postgresql://, mysql://, redis://…). */
export function isDsn(value: string | null | undefined): boolean {
  return DSN_SCHEME_RE.test((value ?? '').trim());
}

/** True only for an http(s) URL — the only kind the UI may render as a link. */
export function isHttpUrl(value: string | null | undefined): boolean {
  return /^https?:\/\//i.test((value ?? '').trim());
}

/**
 * The URL with its userinfo (user:password@) replaced by `***@`. Scheme, hosts,
 * path and query stay readable so a user can still tell connections apart.
 */
export function maskDsn(value: string | null | undefined): string {
  const text = value ?? '';
  return text.replace(/^([a-z][a-z0-9+.-]*:\/\/)[^/?#]*@/i, '$1***@');
}

/**
 * True when a value is a backend mask placeholder, not a real secret:
 * connectors answer `<redacted>`, Sources `********`, and a masked URI keeps
 * its shape with the password starred (`mongodb://alice:****@host`).
 * Sending such a value back on save means "unchanged".
 */
export function isMaskedSecret(value: string | null | undefined): boolean {
  const text = (value ?? '').trim();
  if (!text) return false;
  return text.includes('<redacted>') || /\*{3,}/.test(text);
}

/**
 * What the UI shows as a connection's address: the backend's `display_url`
 * (masked URI, no userinfo — A8), else the built-in's upstream_url, else the
 * url ('Built-in' for the builtin:// marker). Always userinfo-masked again.
 */
export function connectorDisplayUrl(
  c: Pick<ConnectorResponse, 'url' | 'upstream_url' | 'display_url'>,
): string {
  const shown = c.display_url?.trim() || c.upstream_url?.trim() || (c.url === 'builtin://' ? 'Built-in' : c.url);
  return maskDsn(shown);
}
