import { ApiError } from '@/lib/api/client';

/**
 * A 4xx body split into per-field messages and one general message.
 *
 * FastAPI 422s carry `detail: [{loc: ['body', 'connection_config', 'uri'], msg}]`;
 * other refusals (egress/SSRF 422, quota 429, 400) carry a string `detail`.
 * Field keys are dotted paths below `body` ("name", "connection_config.uri").
 * Unknown shapes fall back to the error's message as the general text.
 */
export interface ApiFieldErrors {
  fields: Record<string, string>;
  /** Raw general text (callers decide how to present it), null when none. */
  general: string | null;
}

interface PydanticIssue {
  loc?: unknown;
  msg?: unknown;
}

function cleanMsg(msg: string): string {
  return msg.replace(/^Value error,\s*/i, '');
}

export function parseApiFieldErrors(error: unknown): ApiFieldErrors {
  const out: ApiFieldErrors = { fields: {}, general: null };
  if (!error) return out;
  const body = error instanceof ApiError ? error.body : undefined;
  const detail = body && typeof body === 'object' ? (body as { detail?: unknown }).detail : undefined;
  const general: string[] = [];

  if (Array.isArray(detail)) {
    for (const item of detail as PydanticIssue[]) {
      const msg = typeof item?.msg === 'string' ? cleanMsg(item.msg) : '';
      if (!msg) continue;
      const loc = Array.isArray(item.loc) ? item.loc.map(String) : [];
      const path = (loc[0] === 'body' ? loc.slice(1) : []).join('.');
      if (path && !out.fields[path]) out.fields[path] = msg;
      else if (!path) general.push(msg);
    }
  } else if (detail && typeof detail === 'object') {
    // Tolerated shape: {detail: {field: "connection_config.uri", message: "..."}}
    const d = detail as { field?: unknown; message?: unknown };
    if (typeof d.field === 'string' && typeof d.message === 'string') out.fields[d.field] = d.message;
    else if (typeof d.message === 'string') general.push(d.message);
  } else if (typeof detail === 'string' && detail) {
    general.push(detail);
  }

  if (!general.length && !Object.keys(out.fields).length) {
    const msg = error instanceof Error ? error.message : typeof error === 'string' ? error : '';
    if (msg) general.push(msg);
  }
  out.general = general.length ? general.join('; ') : null;
  return out;
}

/** The connection_config.* field errors, keyed by the config key ("uri"). */
export function connectionConfigErrors(errors: ApiFieldErrors): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [path, msg] of Object.entries(errors.fields)) {
    if (path.startsWith('connection_config.')) out[path.slice('connection_config.'.length)] = msg;
  }
  return out;
}
