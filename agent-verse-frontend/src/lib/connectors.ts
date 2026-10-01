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
