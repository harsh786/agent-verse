/**
 * Friendly messages for connection / driver errors (connectors, ingestion
 * Sources). Backends and drivers return raw text — pymongo's
 * ServerSelectionTimeoutError carries a whole TopologyDescription with every
 * member host — so the UI shows a short, known reason and keeps the raw text,
 * sanitised (no credentials, no hosts), behind a "details" toggle.
 */

export interface FriendlyError {
  /** Short, user-facing reason. */
  message: string;
  /** Sanitised raw text for the details toggle, null when it adds nothing. */
  detail: string | null;
}

const MAX_DETAIL = 800;
/** An unmatched message longer than this is summarised instead of shown as-is. */
const MAX_PLAIN_MESSAGE = 160;

// Ordered: the first match wins. More specific causes come before the generic
// ones they are often wrapped in (a TLS or refused error also says "Timeout").
const RULES: Array<[RegExp, string | ((m: RegExpMatchArray) => string)]> = [
  [/ssrf|disallowed (url|host|address)|egress|rejected by the ssrf|(private|internal|loopback|link-local|reserved|non-public) (address|host|ip|network|destination)|not a public/i,
    'This address is blocked: connections to private, internal or loopback hosts are not allowed.'],
  [/no connection uri|configure credentials|credentials_required|needs a connection uri or a host/i,
    'No connection URI is configured for this connection.'],
  [/must start with mongodb/i, 'The connection URI must start with mongodb:// or mongodb+srv://.'],
  [/auth(entication)? mechanism .*not allowed/i, 'That authentication mechanism is not allowed.'],
  [/uri option.*not allowed/i, 'The connection URI uses an option that is not allowed.'],
  [/x509.*(needs|requires)|tls_client_cert and tls_client_private_key/i,
    'X.509 authentication needs a client certificate and its private key.'],
  [/pem-encoded|must be a pem/i, 'The CA certificate must be PEM-encoded (-----BEGIN CERTIFICATE-----).'],
  [/pymongo not installed|dependency_missing/i, 'The MongoDB driver is not available on the server.'],
  [/credentials were rejected/i, 'The credentials were rejected.'],
  [/authentication failed|auth failed|bad auth|authenticationfailed|not authori[sz]ed|unauthori[sz]ed|scram failure/i,
    'Authentication failed: check the username, password and auth source.'],
  [/\bssl\b|\btls\b|certificate|handshake/i,
    'TLS handshake failed: check the TLS setting and the CA certificate.'],
  [/dns|getaddrinfo|name or service not known|nodename nor servname|enotfound|could not resolve|no address associated/i,
    'The host name could not be resolved.'],
  [/connection refused|econnrefused|errno 61|errno 111|actively refused/i,
    'The server refused the connection: check the host and port.'],
  [/timed out|timeout|deadline exceeded/i,
    'Timed out reaching the server: check the host, port and network access (firewall / IP allow-list).'],
  [/quota|rate limit|too many requests/i, 'A quota or rate limit was reached. Try again later.'],
  [/HTTP (\d{3})/, (m) => `The endpoint answered HTTP ${m[1]}.`],
];

/** Raw error text with credentials and hosts removed (safe for a details panel). */
export function sanitizeErrorDetail(raw: string): string {
  let t = raw;
  // key=value / key: value secrets
  t = t.replace(/\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)(["']?\s*[:=]\s*["']?)[^\s"',;}&)]+/gi, '$1$2***');
  // whole URIs: keep the scheme only (userinfo and hosts are both private)
  t = t.replace(/\b([a-z][a-z0-9+.-]*:\/\/)[^\s'"<>)\]]+/gi, '$1<host>');
  // ('host', 27017) tuples (pymongo ServerDescription)
  t = t.replace(/\(\s*'[^']*'\s*,\s*\d+\s*\)/g, '(<host>)');
  // [v6]:port
  t = t.replace(/\[[0-9a-f:.]+\](:\d+)?/gi, '<host>');
  // v4 with optional port
  t = t.replace(/\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b/g, '<host>');
  // name:port
  t = t.replace(/\b[a-z0-9_][a-z0-9_.-]*:\d{2,5}\b/gi, '<host>');
  // dotted domain names (lower-case: class paths like pymongo.errors go too — safe side)
  t = t.replace(/\b(?:[a-z0-9_](?:[a-z0-9_-]*[a-z0-9])?\.)+[a-z]{2,}\b\.?/g, '<host>');
  t = t.replace(/\blocalhost\b/gi, '<host>');
  return t.length > MAX_DETAIL ? `${t.slice(0, MAX_DETAIL)}…` : t;
}

function rawText(error: unknown): string {
  if (typeof error === 'string') return error;
  if (error instanceof Error) return error.message;
  if (error && typeof error === 'object') {
    const o = error as { message?: unknown; error?: unknown; detail?: unknown };
    for (const v of [o.message, o.error, o.detail]) if (typeof v === 'string') return v;
  }
  return '';
}

/**
 * A short reason for a connection error plus sanitised details.
 * `fallback` is used when there is no error text at all.
 */
const ERROR_ID_RE = /\s*\(error id ([0-9a-f]{12})\)\s*$/i;
/** A backend-classified message is short and carries no driver dump. */
const MAX_CLASSIFIED_MESSAGE = 400;

export function friendlyConnectionError(error: unknown, fallback = 'The connection failed.'): FriendlyError {
  const full = rawText(error).trim();
  if (!full) return { message: fallback, detail: null };
  // fix/mongo-mcp: tool and /test errors end "(error id <12 hex>)" — the full
  // detail is in the server log under that id, so the id is always shown.
  const errorId = full.match(ERROR_ID_RE)?.[1];
  const raw = errorId ? full.replace(ERROR_ID_RE, '') : full;
  const withId = (message: string) => (errorId ? `${message.replace(/\.$/, '')} (error id ${errorId})` : message);
  const detail = sanitizeErrorDetail(raw);
  // A message the backend already classified (it has an error id, nothing
  // private in it, no driver dump) is the best reason there is: keep it.
  if (errorId && detail === raw && raw.length <= MAX_CLASSIFIED_MESSAGE && !/topology/i.test(raw)) {
    return { message: withId(raw), detail: null };
  }
  for (const [re, msg] of RULES) {
    const m = raw.match(re);
    if (m) {
      const message = typeof msg === 'string' ? msg : msg(m);
      return { message: withId(message), detail: detail === message ? null : detail };
    }
  }
  if (detail.length <= MAX_PLAIN_MESSAGE && !detail.includes('\n')) return { message: withId(detail), detail: null };
  return { message: withId('The connection failed.'), detail };
}
