import { useState, useCallback, useRef, useEffect } from 'react';
import { useLocation, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';
import { Eye, EyeOff, Plus, Trash2, ExternalLink, CheckCircle2, XCircle, Loader2, Info } from 'lucide-react';
import { ApiError, connectorsApi, type ConnectorResponse, type CatalogAuthField, type ConnectorTestResult } from '@/lib/api/client';
import { ConfirmModal } from '@/components/ui/ConfirmModal';
import { connectorLabel, connectorTypeLabel, isDsn, isHttpUrl, isMaskedSecret, maskDsn } from '@/lib/connectors';
import { JARVISPageShell } from '@/components/ui/JARVISPageShell';
import { JARVISStagger } from '@/components/ui/JARVISPageShell';

// ── Auth-type field definitions ─────────────────────────────────────────────

type AuthFieldType = 'text' | 'password' | 'textarea' | 'email' | 'url' | 'checkbox' | 'file' | 'select';

const AUTH_FIELD_TYPES: readonly AuthFieldType[] = ['text', 'password', 'textarea', 'email', 'url', 'checkbox', 'file', 'select'];

interface AuthField {
  key: string;
  label: string;
  placeholder: string;
  /** checkbox stores 'true' / '' ; file reads the chosen file's text (or accepts a paste). */
  type: AuthFieldType;
  required: boolean;
  hint?: string;
  /** Choices for a `select` field. */
  options?: { value: string; label: string }[];
  /** File-picker filter for a `file` field. */
  accept?: string;
  /** Render the field only when this holds for the current values (e.g. TLS-only options). */
  visibleWhen?: (values: Record<string, string>) => boolean;
  /** Warning shown while a checkbox is on (a security-weakening switch). */
  warning?: string;
}

const TRUE_VALUES = new Set(['1', 'true', 'yes', 'on']);
const isTruthy = (v: string | undefined) => TRUE_VALUES.has(String(v ?? '').trim().toLowerCase());

interface AuthTypeConfig {
  label: string;
  description: string;
  color: string;
  fields: AuthField[];
}

const AUTH_TYPE_CONFIGS: Record<string, AuthTypeConfig> = {
  bearer: {
    label: 'Bearer Token',
    description: 'Sends Authorization: Bearer <token> header',
    color: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
    fields: [
      {
        key: 'token',
        label: 'Access Token',
        placeholder: 'ghp_xxxxxxxxxxxx or sk-ant-...',
        type: 'password',
        required: true,
        hint: 'The token sent as "Authorization: Bearer <token>"',
      },
    ],
  },
  api_key: {
    label: 'API Key',
    description: 'Sends the key in a custom header (default: X-API-Key)',
    color: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400',
    fields: [
      {
        key: 'api_key',
        label: 'API Key',
        placeholder: 'your-api-key-here',
        type: 'password',
        required: true,
        hint: 'The API key value',
      },
      {
        key: 'header_name',
        label: 'Header Name',
        placeholder: 'X-API-Key',
        type: 'text',
        required: false,
        hint: 'HTTP header to send the key in. Default: X-API-Key',
      },
    ],
  },
  basic: {
    label: 'Basic Auth',
    description: 'Sends Authorization: Basic base64(username:password)',
    color: 'bg-[#0F1826] text-[#F0F6FF] dark:bg-[#1A1F2E]/30 dark:text-[#94A3B8]',
    fields: [
      {
        key: 'username',
        label: 'Username / Email',
        placeholder: 'you@yourcompany.com',
        type: 'email',
        required: true,
        hint: 'For JIRA/Atlassian: use your full email address',
      },
      {
        key: 'password',
        label: 'Password / API Token',
        placeholder: 'ATATT3xFfGF0...',
        type: 'password',
        required: true,
        hint: 'For JIRA: use an Atlassian API Token, not your account password',
      },
    ],
  },
  connection_string: {
    label: 'Connection String',
    description: 'Database connection URI (MongoDB, PostgreSQL, …) — stored as a secret and never shown in plain text',
    color: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400',
    fields: [
      {
        key: 'uri',
        label: 'Connection URI',
        placeholder: 'mongodb+srv://cluster0.example.mongodb.net/',
        type: 'password',
        required: true,
        hint: 'mongodb:// or mongodb+srv://. Put the credentials in the fields below instead (no URL-escaping needed).',
      },
      {
        key: 'username',
        label: 'Username',
        placeholder: 'app-user',
        type: 'text',
        required: false,
      },
      {
        key: 'password',
        label: 'Password',
        placeholder: '••••••••',
        type: 'password',
        required: false,
      },
      {
        key: 'database',
        label: 'Database',
        placeholder: 'orders',
        type: 'text',
        required: false,
        hint: 'Used when a tool call names no database (falls back to the URI path).',
      },
    ],
  },
  oauth_ac: {
    label: 'OAuth 2.0 (Authorization Code)',
    description: 'Redirects user to authorize — click "Start OAuth Flow" after registering',
    color: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
    fields: [
      {
        key: 'client_id',
        label: 'Client ID',
        placeholder: 'your-oauth-client-id',
        type: 'text',
        required: true,
        hint: 'OAuth application client ID from the provider',
      },
      {
        key: 'client_secret',
        label: 'Client Secret',
        placeholder: 'your-client-secret',
        type: 'password',
        required: true,
        hint: 'OAuth application client secret — keep this private',
      },
      {
        key: 'scopes',
        label: 'Scopes',
        placeholder: 'read:jira-work write:jira-work',
        type: 'text',
        required: false,
        hint: 'Space-separated list of OAuth scopes to request',
      },
    ],
  },
  pkce: {
    label: 'OAuth 2.0 PKCE',
    description: 'Authorization Code with PKCE — no client secret required',
    color: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
    fields: [
      {
        key: 'client_id',
        label: 'Client ID',
        placeholder: 'your-oauth-client-id',
        type: 'text',
        required: true,
        hint: 'OAuth application client ID',
      },
      {
        key: 'scopes',
        label: 'Scopes',
        placeholder: 'repo read:org',
        type: 'text',
        required: false,
        hint: 'Space-separated OAuth scopes to request',
      },
    ],
  },
  oauth_cc: {
    label: 'OAuth 2.0 Client Credentials',
    description: 'Machine-to-machine flow — no user login required',
    color: 'bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-400',
    fields: [
      {
        key: 'client_id',
        label: 'Client ID',
        placeholder: 'your-client-id',
        type: 'text',
        required: true,
      },
      {
        key: 'client_secret',
        label: 'Client Secret',
        placeholder: 'your-client-secret',
        type: 'password',
        required: true,
      },
      {
        key: 'token_url',
        label: 'Token URL',
        placeholder: 'https://auth.provider.com/oauth/token',
        type: 'url',
        required: true,
        hint: 'The OAuth token endpoint URL',
      },
      {
        key: 'scopes',
        label: 'Scopes',
        placeholder: 'api:read api:write',
        type: 'text',
        required: false,
      },
    ],
  },
  hmac: {
    label: 'HMAC Signature',
    description: 'Verifies requests via HMAC-SHA256 signature',
    color: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400',
    fields: [
      {
        key: 'secret',
        label: 'Signing Secret',
        placeholder: 'your-hmac-secret-key',
        type: 'password',
        required: true,
        hint: 'Shared secret used to compute and verify HMAC signatures',
      },
    ],
  },
  mtls: {
    label: 'Mutual TLS (mTLS)',
    description: 'Client certificate authentication',
    color: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
    fields: [
      {
        key: 'certificate',
        label: 'Client Certificate (PEM)',
        placeholder: '-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----',
        type: 'textarea',
        required: true,
        hint: 'PEM-encoded client certificate',
      },
      {
        key: 'private_key',
        label: 'Private Key (PEM)',
        placeholder: '-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----',
        type: 'textarea',
        required: true,
        hint: 'PEM-encoded private key for the certificate',
      },
    ],
  },
  custom_header: {
    label: 'Custom Headers',
    description: 'Send arbitrary HTTP headers',
    color: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
    fields: [], // Dynamic key-value pairs — handled separately
  },
  none: {
    label: 'No Auth',
    description: 'Unauthenticated connection',
    color: 'bg-muted text-muted-foreground',
    fields: [],
  },
};

// ── MongoDB connection fields (the built-in handler's credential keys) ───────
// Keys match app/mcp/servers/mongodb_server.py: uri, username, password,
// auth_source, auth_mechanism, database, tls, tls_ca_pem,
// tls_allow_invalid_certificates.

const mongoTlsOn = (v: Record<string, string>) =>
  isTruthy(v.tls) || !!v.tls_ca_pem?.trim() || isTruthy(v.tls_allow_invalid_certificates);

const MONGODB_AUTH_FIELDS: AuthField[] = [
  AUTH_TYPE_CONFIGS.connection_string.fields[0], // uri (masked)
  { key: 'username', label: 'Username', placeholder: 'app-user', type: 'text', required: false },
  { key: 'password', label: 'Password', placeholder: '••••••••', type: 'password', required: false },
  {
    key: 'auth_source', label: 'Auth source', placeholder: 'admin', type: 'text', required: false,
    hint: 'Database that holds the user (usually admin).',
  },
  {
    key: 'auth_mechanism', label: 'Auth mechanism', placeholder: '', type: 'select', required: false,
    options: [
      { value: '', label: 'Default (SCRAM)' },
      { value: 'SCRAM-SHA-256', label: 'SCRAM-SHA-256' },
      { value: 'SCRAM-SHA-1', label: 'SCRAM-SHA-1' },
      { value: 'PLAIN', label: 'PLAIN (LDAP)' },
    ],
  },
  AUTH_TYPE_CONFIGS.connection_string.fields[3], // database
  {
    key: 'tls', label: 'Use TLS', placeholder: '', type: 'checkbox', required: false,
    hint: 'mongodb+srv:// URIs (Atlas) use TLS automatically.',
  },
  {
    key: 'tls_ca_pem', label: 'CA certificate (PEM, optional)', placeholder: '-----BEGIN CERTIFICATE-----',
    type: 'file', required: false, accept: '.pem,.crt,.cer,text/plain', visibleWhen: mongoTlsOn,
    hint: 'Verify the server against this CA instead of the system trust store. Paste it or choose a file.',
  },
  {
    key: 'tls_allow_invalid_certificates', label: 'Allow invalid certificates', placeholder: '',
    type: 'checkbox', required: false, visibleWhen: mongoTlsOn,
    warning: 'This disables server certificate verification: anyone on the network path can impersonate the database. Use it only for local development.',
  },
];

/** auth_config keys that hold a connection URI (the handler accepts any of them). */
const URI_KEYS = ['uri', 'connection_string', 'url', 'mongodb_uri', 'dsn'];

// ── Connector-specific URL hints ────────────────────────────────────────────

const CONNECTOR_URL_MAP: Record<string, { url: string; hint: string; label: string }> = {
  jira:           { url: 'https://yourcompany.atlassian.net', hint: 'Replace "yourcompany" with your Atlassian subdomain', label: 'JIRA Base URL' },
  confluence:     { url: 'https://yourcompany.atlassian.net', hint: 'Same domain as JIRA for Atlassian Cloud', label: 'Confluence Base URL' },
  github:         { url: 'https://api.github.com', hint: 'GitHub REST API. For MCP Server use: https://api.githubcopilot.com/mcp/', label: 'GitHub API URL' },
  gitlab:         { url: 'https://gitlab.com', hint: 'For self-hosted: https://gitlab.yourcompany.com', label: 'GitLab URL' },
  slack:          { url: 'https://slack.com/api', hint: 'Always use this URL for Slack API calls', label: 'Slack API URL' },
  salesforce:     { url: 'https://yourinstance.salesforce.com', hint: 'Replace with your Salesforce instance domain', label: 'Salesforce Instance URL' },
  hubspot:        { url: 'https://api.hubapi.com', hint: 'Standard HubSpot API URL', label: 'HubSpot API URL' },
  linear:         { url: 'https://api.linear.app', hint: 'Standard Linear API URL', label: 'Linear API URL' },
  notion:         { url: 'https://api.notion.com/v1', hint: 'Standard Notion API URL', label: 'Notion API URL' },
  datadog:        { url: 'https://api.datadoghq.com', hint: 'For EU: https://api.datadoghq.eu', label: 'Datadog API URL' },
  sentry:         { url: 'https://sentry.io/api/0', hint: 'For self-hosted: https://sentry.yourcompany.com/api/0', label: 'Sentry API URL' },
  stripe:         { url: 'https://api.stripe.com', hint: 'Always use this URL for Stripe API calls', label: 'Stripe API URL' },
  postgres:       { url: 'postgresql://user:password@localhost:5432/dbname', hint: 'Replace with your PostgreSQL connection string', label: 'PostgreSQL DSN' },
  mongodb:        { url: 'mongodb://localhost:27017', hint: 'Replace with your MongoDB connection URI', label: 'MongoDB URI' },
  snowflake:      { url: 'https://yourorg.snowflakecomputing.com', hint: 'Replace with your Snowflake account identifier', label: 'Snowflake URL' },
  aws:            { url: 'https://amazonaws.com', hint: 'Region-specific: https://s3.us-east-1.amazonaws.com', label: 'AWS Endpoint' },
  gcp:            { url: 'https://googleapis.com', hint: 'Standard Google Cloud API base URL', label: 'GCP API URL' },
  teams:          { url: 'https://graph.microsoft.com/v1.0', hint: 'Microsoft Graph API URL for Teams', label: 'MS Graph API URL' },
  zendesk:        { url: 'https://yoursubdomain.zendesk.com', hint: 'Replace "yoursubdomain" with your Zendesk subdomain', label: 'Zendesk URL' },
  intercom:       { url: 'https://api.intercom.io', hint: 'Standard Intercom API URL', label: 'Intercom API URL' },
  quickbooks:     { url: 'https://quickbooks.api.intuit.com', hint: 'Production QuickBooks API URL', label: 'QuickBooks API URL' },
  asana:          { url: 'https://app.asana.com/api/1.0', hint: 'Standard Asana API URL', label: 'Asana API URL' },
  monday:         { url: 'https://api.monday.com/v2', hint: 'Standard monday.com API URL', label: 'monday.com API URL' },
  pagerduty:      { url: 'https://api.pagerduty.com', hint: 'Standard PagerDuty API URL', label: 'PagerDuty API URL' },
  okta:           { url: 'https://yourorg.okta.com', hint: 'Replace "yourorg" with your Okta subdomain', label: 'Okta Domain URL' },
  twilio:         { url: 'https://api.twilio.com', hint: 'Standard Twilio API URL', label: 'Twilio API URL' },
  sendgrid:       { url: 'https://api.sendgrid.com', hint: 'Standard SendGrid API URL', label: 'SendGrid API URL' },
};

// ── Per-connector auth field hints ──────────────────────────────────────────

const CONNECTOR_AUTH_HINTS: Record<string, Record<string, string>> = {
  jira: {
    username: 'Your Atlassian account email — e.g. you@yourcompany.com',
    password: 'Generate at: id.atlassian.com → Security → API Tokens. NOT your login password.',
  },
  confluence: {
    username: 'Your Atlassian account email',
    password: 'Same API token as JIRA — generated at id.atlassian.com → Security → API Tokens',
  },
  github: {
    token: 'Generate at: github.com → Settings → Developer settings → Personal access tokens → Tokens (classic). Needs repo and read:org scopes for the GitHub MCP Server.',
  },
  gitlab: {
    token: 'Generate at: GitLab → User Settings → Access Tokens',
  },
  datadog: {
    api_key: 'Found in Datadog → Organization Settings → API Keys',
  },
  stripe: {
    token: 'Found in Stripe Dashboard → Developers → API Keys. Use sk_live_... for production.',
  },
  linear: {
    api_key: 'Generate at: linear.app → Settings → API → Personal API Keys',
  },
  notion: {
    token: 'Create an integration at: notion.so/my-integrations → copy the Internal Integration Token',
  },
  slack: {
    token: 'Create a Slack app at api.slack.com/apps → OAuth & Permissions → Bot User OAuth Token (xoxb-...)',
  },
  sentry: {
    token: 'Generate at: sentry.io → Settings → Account → API → Auth Tokens',
  },
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function getConnectorKey(name: string): string {
  return name.toLowerCase().replace(/[-_\s]/g, '').split('.')[0];
}

function getUrlConfig(connectorName: string) {
  const key = getConnectorKey(connectorName);
  for (const [connKey, config] of Object.entries(CONNECTOR_URL_MAP)) {
    if (key.includes(connKey) || connKey.includes(key)) return config;
  }
  return null;
}

function getFieldHint(connectorName: string, fieldKey: string, defaultHint?: string): string {
  const key = getConnectorKey(connectorName);
  for (const [connKey, hints] of Object.entries(CONNECTOR_AUTH_HINTS)) {
    if (key.includes(connKey) || connKey.includes(key)) {
      return hints[fieldKey] ?? defaultHint ?? '';
    }
  }
  return defaultHint ?? '';
}

// ── Sub-components ────────────────────────────────────────────────────────────

function PasswordInput({
  value,
  onChange,
  placeholder,
  id,
  'aria-describedby': describedBy,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  id: string;
  'aria-describedby'?: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <input
        id={id}
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-describedby={describedBy}
        autoComplete="new-password"
        className="w-full border border-input rounded-lg px-3 py-2 pr-9 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
        aria-label={visible ? 'Hide' : 'Show'}
      >
        {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

function HintText({ id, text }: { id: string; text: string }) {
  return (
    <p id={id} className="flex items-start gap-1.5 text-xs text-muted-foreground mt-1">
      <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
      {text}
    </p>
  );
}

// ── Smart Auth Fields Component ───────────────────────────────────────────────

function SmartAuthFields({
  authType,
  fields,
  authValues,
  connectorName,
  onChange,
}: {
  authType: string;
  /** Resolved fields for this auth type (see resolveAuthFields). */
  fields: AuthField[];
  authValues: Record<string, string>;
  connectorName: string;
  onChange: (values: Record<string, string>) => void;
}) {
  const setField = (key: string, value: string) =>
    onChange({ ...authValues, [key]: value });

  // custom_header: dynamic key-value pairs
  if (authType === 'custom_header') {
    const pairs = Object.entries(authValues).length
      ? Object.entries(authValues)
      : [['Authorization', '']];

    const updatePair = (idx: number, k: string, v: string) => {
      const newPairs = [...pairs];
      newPairs[idx] = [k, v];
      onChange(Object.fromEntries(newPairs.filter(([key]) => key.trim())));
    };
    const addPair = () =>
      onChange({ ...authValues, '': '' });
    const removePair = (idx: number) => {
      const newPairs = pairs.filter((_, i) => i !== idx);
      onChange(Object.fromEntries(newPairs.filter(([key]) => key.trim())));
    };

    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            Add HTTP headers that will be sent with every request
          </p>
          <button
            type="button"
            onClick={addPair}
            className="flex items-center gap-1 text-xs text-primary hover:opacity-80"
          >
            <Plus className="h-3.5 w-3.5" /> Add header
          </button>
        </div>
        {pairs.map(([k, v], idx) => (
          <div key={idx} className="flex gap-2 items-center">
            <input
              value={k}
              onChange={(e) => updatePair(idx, e.target.value, v)}
              placeholder="Header-Name"
              aria-label="Header name"
              className="flex-1 border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
            />
            <PasswordInput
              id={`custom-header-val-${idx}`}
              value={v}
              onChange={(val) => updatePair(idx, k, val)}
              placeholder="header-value"
            />
            {pairs.length > 1 && (
              <button
                type="button"
                onClick={() => removePair(idx)}
                className="text-destructive hover:opacity-70 flex-shrink-0"
                aria-label="Remove header"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            )}
          </div>
        ))}
        <div className="rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-3 text-xs text-amber-800 dark:text-amber-300">
          <strong>JIRA example:</strong> Header Name = <code>Authorization</code>, Value = <code>Basic {'{base64(email:token)}'}</code>
          <br />
          <span className="text-amber-600 dark:text-amber-400">
            Tip: Use "Basic Auth" type instead — it encodes automatically.
          </span>
        </div>
      </div>
    );
  }

  // none
  if (!fields.length) {
    return (
      <div className="rounded-lg bg-muted/40 border border-border px-4 py-3 text-sm text-muted-foreground">
        No authentication required for this connector type.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {fields
        .filter((field) => !field.visibleWhen || field.visibleWhen(authValues))
        .map((field) => (
          <AuthFieldInput
            key={field.key}
            field={field}
            value={authValues[field.key] ?? ''}
            hint={getFieldHint(connectorName, field.key, field.hint)}
            onChange={(v) => setField(field.key, v)}
          />
        ))}
    </div>
  );
}

/** A chosen file's text (FileReader: Blob.text() is missing in some runtimes). */
function readFileText(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('Could not read the file'));
    reader.readAsText(file);
  });
}

const INPUT_CLS =
  'w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none';

/** One credential field — text/password/email/url/textarea/checkbox/file/select. */
function AuthFieldInput({
  field,
  value,
  hint,
  onChange,
}: {
  field: AuthField;
  value: string;
  hint: string;
  onChange: (v: string) => void;
}) {
  const hintId = `hint-${field.key}`;
  const inputId = `auth-${field.key}`;
  const describedBy = hint ? hintId : undefined;

  if (field.type === 'checkbox') {
    const checked = isTruthy(value);
    return (
      <div>
        <label htmlFor={inputId} className="flex items-center gap-2 text-sm font-medium">
          <input
            id={inputId}
            type="checkbox"
            checked={checked}
            onChange={(e) => onChange(e.target.checked ? 'true' : '')}
            aria-describedby={describedBy}
            className="h-4 w-4 rounded border-input accent-primary"
          />
          {field.label}
        </label>
        {hint && <HintText id={hintId} text={hint} />}
        {checked && field.warning && (
          <p className="mt-1 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
            {field.warning}
          </p>
        )}
      </div>
    );
  }

  return (
    <div>
      <label htmlFor={inputId} className="block text-sm font-medium mb-1">
        {field.label}
        {field.required && <span className="text-destructive ml-1">*</span>}
      </label>
      {field.type === 'password' ? (
        <PasswordInput
          id={inputId}
          value={value}
          onChange={onChange}
          placeholder={field.placeholder}
          aria-describedby={describedBy}
        />
      ) : field.type === 'textarea' || field.type === 'file' ? (
        <>
          <textarea
            id={inputId}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={field.placeholder}
            rows={4}
            spellCheck={false}
            aria-describedby={describedBy}
            className={`${INPUT_CLS} font-mono resize-y`}
          />
          {field.type === 'file' && (
            <input
              type="file"
              accept={field.accept}
              aria-label={`Upload ${field.label}`}
              onChange={async (e) => {
                const input = e.currentTarget;
                const file = input.files?.[0];
                if (file) onChange(await readFileText(file));
                input.value = '';
              }}
              className="mt-1 block text-xs text-muted-foreground file:mr-2 file:rounded file:border file:border-border file:bg-muted file:px-2 file:py-1 file:text-xs"
            />
          )}
        </>
      ) : field.type === 'select' ? (
        <select
          id={inputId}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-describedby={describedBy}
          className={INPUT_CLS}
        >
          {(field.options ?? []).map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
          {/* A stored value not in the list stays selectable. */}
          {value && !(field.options ?? []).some((o) => o.value === value) && (
            <option value={value}>{value}</option>
          )}
        </select>
      ) : (
        <input
          id={inputId}
          type={field.type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder}
          aria-describedby={describedBy}
          className={INPUT_CLS}
        />
      )}
      {hint && <HintText id={hintId} text={hint} />}
    </div>
  );
}

function unknownAuthTypeConfig(authType: string, authValues: Record<string, string>): AuthTypeConfig {
  return {
    label: authType,
    description: 'Auth type not known to this UI — stored values are shown masked',
    color: 'bg-muted text-muted-foreground',
    fields: Object.keys(authValues)
      .filter((key) => key.trim())
      .map((key) => ({ key, label: key, placeholder: '', type: 'password' as const, required: false })),
  };
}

function isMongoConnector(connectorName: string, values: Record<string, string>): boolean {
  return (
    getConnectorKey(connectorName).includes('mongo') ||
    Object.values(values).some((v) => /^mongodb(\+srv)?:\/\//i.test(String(v ?? '')))
  );
}

/** A GET /connectors/catalog auth field as a renderable field (unknown types render as text). */
function catalogFieldToAuthField(f: CatalogAuthField): AuthField {
  const type = AUTH_FIELD_TYPES.includes(f.field_type as AuthFieldType) ? (f.field_type as AuthFieldType) : 'text';
  return {
    key: f.key,
    label: f.label || f.key,
    placeholder: f.placeholder ?? '',
    type,
    required: Boolean(f.required),
    hint: f.hint || undefined,
    options: f.options?.map((o) => (typeof o === 'string' ? { value: o, label: o } : o)),
  };
}

/**
 * The credential fields to collect for an auth type.
 *
 * - connection_string to MongoDB gets every option the built-in handler reads.
 * - Catalog fields (from the catalog entry the form was opened from) win for
 *   ordinary types; for connection_string they only ADD keys — the catalog's
 *   generic `url` field is the same URI and is never collected twice.
 * - The URI field binds to whichever URI key the stored config already uses
 *   (an older registration may hold it under `url`), so edit shows it.
 * - An auth type this UI doesn't know shows its stored keys, masked.
 */
function resolveAuthFields(
  authType: string,
  connectorName: string,
  authValues: Record<string, string>,
  catalogFields: CatalogAuthField[] = [],
): AuthField[] {
  let base: AuthField[] =
    authType === 'connection_string' && isMongoConnector(connectorName, authValues)
      ? MONGODB_AUTH_FIELDS
      : AUTH_TYPE_CONFIGS[authType]?.fields ?? unknownAuthTypeConfig(authType, authValues).fields;
  if (authType === 'connection_string') {
    const uriKey = URI_KEYS.find((k) => k in authValues) ?? 'uri';
    base = base.map((f) => (f.key === 'uri' ? { ...f, key: uriKey } : f));
  }
  if (!catalogFields.length) return base;
  const fromCatalog = catalogFields.map(catalogFieldToAuthField);
  if (authType !== 'connection_string') return fromCatalog;
  const have = new Set(base.map((f) => f.key));
  return [...base, ...fromCatalog.filter((f) => !have.has(f.key) && !URI_KEYS.includes(f.key))];
}

// ── Auth type selector with colored badge ────────────────────────────────────

function AuthTypeSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const config = AUTH_TYPE_CONFIGS[value];
  return (
    <div>
      <label htmlFor="auth-type-select" className="block text-sm font-medium mb-1">
        Auth Type <span className="text-destructive">*</span>
      </label>
      <div className="flex items-center gap-2">
        <select
          id="auth-type-select"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="flex-1 border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
        >
          {Object.entries(AUTH_TYPE_CONFIGS).map(([type, cfg]) => (
            <option key={type} value={type}>
              {cfg.label}
            </option>
          ))}
          {/* A stored type this UI doesn't list must still be the selected one —
              a controlled <select> with an unknown value shows the first option. */}
          {value && !config && <option value={value}>{value}</option>}
        </select>
        {config && (
          <span className={`text-xs px-2 py-1 rounded-full font-medium whitespace-nowrap ${config.color}`}>
            {config.label}
          </span>
        )}
      </div>
      {config && (
        <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
          <Info className="h-3.5 w-3.5 flex-shrink-0" />
          {config.description}
        </p>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface FormState {
  name: string;
  /** Catalog key of this connection's type (drives URL/auth hints), '' if unknown. */
  connector_type: string;
  /** Human type label shown in the form ("MongoDB"). */
  type_label: string;
  /** Built-in type sent as `type` when CREATING a connection ('' = remote MCP
   *  server). A tenant may hold several connections of one type, each named. */
  builtin_type: string;
  url: string;
  auth_type: string;
  auth_values: Record<string, string>;
  auto_approve: boolean;
}

const EMPTY_FORM: FormState = {
  name: '',
  connector_type: '',
  type_label: '',
  builtin_type: '',
  url: '',
  auth_type: 'bearer',
  auth_values: {},
  auto_approve: false,
};

/** True when the auth values hold a connection URI under any accepted key. */
function hasUriValue(values: Record<string, string>): boolean {
  return URI_KEYS.some((k) => (values[k] ?? '').trim() !== '');
}

/**
 * The top-level `url` to send. A connection-string connector keeps its URI in
 * auth_config (a secret) and sends the built-in marker; only a legacy row whose
 * stored url is a MASKED DSN (unrecoverable here) is sent back unchanged.
 */
function payloadUrl(form: FormState): string {
  if (form.auth_type !== 'connection_string') return form.url.trim();
  if (isDsn(form.url) && isMaskedSecret(form.url)) return form.url;
  return 'builtin://';
}

/**
 * Edit-time migration of a legacy connection-string row that stored its DSN in
 * the top-level url: move it into the masked URI field (it leaves `url`).
 */
function migrateLegacyDsn(form: FormState): FormState {
  if (form.auth_type !== 'connection_string' || !isDsn(form.url) || isMaskedSecret(form.url)) return form;
  if (hasUriValue(form.auth_values)) return { ...form, url: 'builtin://' };
  return { ...form, url: 'builtin://', auth_values: { ...form.auth_values, uri: form.url } };
}

function buildAuthConfig(_authType: string, authValues: Record<string, string>): Record<string, string> {
  // Strip empty values
  return Object.fromEntries(
    Object.entries(authValues).filter(([, v]) => v.trim() !== '')
  );
}

function parseAuthConfigToValues(_authType: string, authConfig: Record<string, string>): Record<string, string> {
  return { ...authConfig };
}

/** POST /connectors/{id}/test outcome, or the request's own failure (4xx/5xx). */
interface TestResult {
  /** null = nothing was contacted (status "not_tested"). */
  reachable: boolean | null;
  status: string;
  latency_ms?: number;
  error?: string;
  detail?: string;
  http_status?: number;
}

function testResultFrom(data: ConnectorTestResult): TestResult {
  return {
    reachable: data.reachable ?? null,
    status: data.status ?? (data.reachable ? 'passed' : 'failed'),
    latency_ms: data.latency_ms,
    error: data.error,
    detail: data.detail,
    http_status: data.http_status,
  };
}

/** A refused/failed test REQUEST (e.g. a 400 SSRF refusal) is a failed test. */
function testResultFromError(e: unknown): TestResult {
  return {
    reachable: false,
    status: 'failed',
    error: e instanceof Error && e.message ? e.message : 'The connection test could not be run',
    http_status: e instanceof ApiError ? e.status : undefined,
  };
}

function TestResultCell({ result }: { result: TestResult }) {
  if (result.reachable === null && result.status === 'not_tested') {
    return (
      <div className="space-y-1">
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-muted text-muted-foreground">
          <Info className="h-3 w-3" /> Not testable
        </span>
        {result.detail && <p className="text-xs text-muted-foreground max-w-xs">{result.detail}</p>}
      </div>
    );
  }
  const ok = result.reachable === true && result.status !== 'failed';
  return (
    <div className="space-y-1">
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
        ok
          ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
          : 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'
      }`}>
        {ok ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
        {ok ? `OK · ${result.latency_ms ?? '?'}ms` : 'Failed'}
      </span>
      {!ok && (
        <p data-testid="test-error" className="text-xs text-destructive max-w-xs break-words">
          {result.error || `Test failed (${result.http_status ?? result.status})`}
        </p>
      )}
    </div>
  );
}

export function ConnectorsRegisteredPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const qc = useQueryClient();
  const location = useLocation();
  const locationState = location.state as { prefill?: any; editConnectorId?: string } | null;
  const prefill = locationState?.prefill;

  const [showModal, setShowModal] = useState(!!prefill);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(() => {
    if (prefill) {
      return {
        name: prefill.name ?? '',
        connector_type: prefill.connector_type ?? '',
        type_label: prefill.type_name ?? prefill.connector_type ?? '',
        builtin_type: prefill.type ?? '',
        // A connection string lives in auth_config only; the catalog's
        // default_url ("mongodb://localhost:27017") is a sample, not a value.
        url: prefill.auth_type === 'connection_string' ? '' : prefill.url ?? prefill.default_url ?? '',
        auth_type: prefill.auth_type ?? 'bearer',
        auth_values: {},
        auto_approve: false,
      };
    }
    return EMPTY_FORM;
  });
  const [formError, setFormError] = useState('');
  const [authFieldOverrides] = useState<CatalogAuthField[]>(() =>
    Array.isArray(prefill?.auth_fields) ? (prefill.auth_fields as CatalogAuthField[]) : []
  );
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  /** An auth-type switch waiting for confirmation because it would drop entered values. */
  const [pendingAuthType, setPendingAuthType] = useState<{ type: string; dropped: string[] } | null>(null);
  const editHandled = useRef(false);

  const { data: connectors = [], isLoading, error } = useQuery({
    queryKey: ['connectors'],
    queryFn: () => connectorsApi.list(),
    enabled: !!apiKey,
  });

  const registerMutation = useMutation({
    mutationFn: () => {
      const auth_config = buildAuthConfig(form.auth_type, form.auth_values);
      const payload = {
        name: form.name.trim(),
        url: payloadUrl(form),
        auth_type: form.auth_type,
        auth_config,
        auto_approve: form.auto_approve,
        // The built-in type of a NEW connection (POST /connectors `type`), so
        // "orders-db" is bound to the MongoDB built-in. It can't change on update.
        ...(!editingId && form.builtin_type ? { type: form.builtin_type } : {}),
      };
      if (editingId) {
        return connectorsApi.update(editingId, payload);
      }
      return connectorsApi.register(payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['connectors'] });
      setShowModal(false);
      setEditingId(null);
      setForm(EMPTY_FORM);
      setFormError('');
    },
    onError: (e: Error) => setFormError(e.message ?? 'Registration failed'),
  });

  const unregisterMutation = useMutation({
    mutationFn: (id: string) => connectorsApi.unregister(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['connectors'] }),
  });

  const testMutation = useMutation({
    mutationFn: (id: string) => connectorsApi.test(id),
    onSuccess: (data, id) =>
      setTestResults((prev) => ({ ...prev, [id]: testResultFrom(data) })),
    // A 4xx (SSRF refusal, validation) used to leave the row at "Not tested".
    onError: (e, id) =>
      setTestResults((prev) => ({ ...prev, [id]: testResultFromError(e) })),
  });

  const openCreate = useCallback(() => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError('');
    setShowModal(true);
  }, []);

  const openEdit = useCallback((c: ConnectorResponse) => {
    setEditingId(c.server_id);
    setForm(migrateLegacyDsn({
      name: connectorLabel(c),
      connector_type: (c.builtin_type ?? '').replace(/^builtin-/, '').split(':')[0],
      type_label: connectorTypeLabel(c),
      builtin_type: c.builtin_type ?? '',
      url: c.url,
      auth_type: c.auth_type ?? 'bearer',
      auth_values: parseAuthConfigToValues(c.auth_type ?? 'bearer', c.auth_config ?? {}),
      auto_approve: Boolean(c.auto_approve),
    }));
    setFormError('');
    setShowModal(true);
  }, []);

  // Auto-open edit modal when navigated here with editConnectorId in location state
  useEffect(() => {
    if (editHandled.current) return;
    if (locationState?.editConnectorId && connectors && connectors.length > 0) {
      const conn = connectors.find(
        (c) =>
          c.server_id === locationState.editConnectorId ||
          (c as any).id === locationState.editConnectorId,
      );
      if (conn) {
        editHandled.current = true;
        openEdit(conn);
      }
    }
  }, [connectors, locationState?.editConnectorId, openEdit]);

  const closeModal = useCallback(() => {
    setShowModal(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError('');
  }, []);

  const urlConfig = getUrlConfig(form.connector_type || form.name);

  // Fields the CURRENT auth type collects, including catalog-provided ones.
  const prefillAuthType: string | undefined = prefill?.auth_type;
  const connectorNameForHints = form.connector_type || form.name;
  const fieldsFor = (authType: string, values: Record<string, string>) =>
    resolveAuthFields(authType, connectorNameForHints, values, authType === prefillAuthType ? authFieldOverrides : []);
  const currentFieldKeys = (authType: string) =>
    new Set(fieldsFor(authType, form.auth_values).map((f) => f.key));
  const authFields = fieldsFor(form.auth_type, form.auth_values);

  const applyAuthType = useCallback((type: string, keep: Set<string>) => {
    setForm((f) => {
      const next: FormState = {
        ...f,
        auth_type: type,
        auth_values: Object.fromEntries(Object.entries(f.auth_values).filter(([k]) => keep.has(k))),
      };
      if (type === 'connection_string') return migrateLegacyDsn(next);
      // Leaving connection_string: the hidden built-in marker is not a URL to edit.
      if (f.auth_type === 'connection_string' && !f.builtin_type && next.url === 'builtin://') next.url = '';
      return next;
    });
  }, []);

  /** Switch auth type keeping the values both types share; confirm before dropping any. */
  const requestAuthTypeChange = (type: string) => {
    if (type === form.auth_type) return;
    const keep = currentFieldKeys(type);
    const dropped = Object.entries(form.auth_values)
      .filter(([k, v]) => k.trim() && v.trim() !== '' && !keep.has(k))
      .map(([k]) => k);
    if (dropped.length) {
      setPendingAuthType({ type, dropped });
      return;
    }
    applyAuthType(type, keep);
  };

  // Each connection needs its own name — several of one type are allowed, but
  // two with the same name can't be told apart in pickers.
  const trimmedName = form.name.trim().toLowerCase();
  const nameTaken =
    !!trimmedName &&
    connectors.some(
      (c) => c.server_id !== editingId && connectorLabel(c).trim().toLowerCase() === trimmedName,
    );

  // Validation
  const isConnectionString = form.auth_type === 'connection_string';
  const canSubmit =
    form.name.trim() &&
    (isConnectionString ? hasUriValue(form.auth_values) : form.url.trim()) &&
    !nameTaken &&
    !registerMutation.isPending;

  return (
    <JARVISPageShell>

      {/* Accessibility: announce loading state */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">{isLoading ? "Loading…" : ""}</div>
    <JARVISStagger className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Registered Connectors</h1>
          <p className="text-muted-foreground text-sm mt-1">
            MCP servers connected to your tenant — {connectors.length} registered
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/connectors/catalog"
            className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm font-medium text-foreground shadow-sm hover:bg-[#1A1F2E] hover:shadow-glow-electric transition-[background-color,box-shadow]"
          >
            Browse Catalog
          </Link>
          <button
            onClick={openCreate}
            className="bg-primary text-primary-foreground px-4 py-2 rounded-lg hover:opacity-90 text-sm font-medium transition-opacity"
          >
            + Register Connector
          </button>
        </div>
      </div>

      {/* Connector table */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-16 gap-2 text-muted-foreground text-sm">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading connectors…
          </div>
        ) : error ? (
          <div className="py-16 text-center text-sm text-destructive">
            Failed to load connectors — check your API key.
          </div>
        ) : connectors.length === 0 ? (
          <div className="py-16 text-center space-y-2">
            <p className="text-muted-foreground text-sm">No connectors registered yet.</p>
            <button
              onClick={openCreate}
              className="text-primary text-sm hover:underline"
            >
              Register your first connector →
            </button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="connectors-table">
              <thead>
                <tr className="border-b border-border bg-muted/40">
                  {['Name', 'URL', 'Auth Type', 'Status', 'Actions'].map((h) => (
                    <th key={h} className="text-left px-4 py-3 font-medium text-muted-foreground">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {connectors.map((c) => {
                  const result = testResults[c.server_id];
                  return (
                    <tr key={c.server_id} className="hover:bg-accent/50 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          {/* server_id is opaque (e.g. builtin-mongodb:<slug>) — encode it. */}
                          <Link
                            to={`/connectors/${encodeURIComponent(c.server_id)}`}
                            className="font-medium text-primary hover:underline"
                          >
                            {connectorLabel(c)}
                          </Link>
                          {connectorTypeLabel(c) &&
                            connectorTypeLabel(c).toLowerCase() !== connectorLabel(c).toLowerCase() && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                              {connectorTypeLabel(c)}
                            </span>
                          )}
                          {c.has_builtin && (
                            <span
                              title="Built-in handler — runs inside AgentVerse, no external MCP server needed"
                              className="inline-flex items-center gap-0.5 rounded-full bg-amber-100 border border-amber-300 px-1.5 py-0.5 text-[10px] font-bold text-amber-800 dark:bg-amber-900/30 dark:border-amber-700 dark:text-amber-300"
                            >
                              ⚡ Built-in
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 font-mono text-[11px] text-muted-foreground break-all" title="Server ID">
                          {c.server_id}
                        </p>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground max-w-xs truncate">
                        {/* Plain text, userinfo masked: a DSN carries its password. */}
                        {c.upstream_url
                          ? maskDsn(c.upstream_url)
                          : c.url === 'builtin://'
                            ? 'Built-in'
                            : maskDsn(c.url)}
                        {c.upstream_url && (
                          <span className="ml-1.5 not-italic font-sans text-[10px] uppercase tracking-wide text-muted-foreground/50">
                            built-in
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {c.auth_type && (
                          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                            AUTH_TYPE_CONFIGS[c.auth_type]?.color ?? 'bg-muted text-muted-foreground'
                          }`}>
                            {AUTH_TYPE_CONFIGS[c.auth_type]?.label ?? c.auth_type}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {result ? (
                          <TestResultCell result={result} />
                        ) : (
                          <span className="text-muted-foreground text-xs">Not tested</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex gap-3 items-center">
                          <button
                            onClick={() => testMutation.mutate(c.server_id)}
                            disabled={testMutation.isPending && testMutation.variables === c.server_id}
                            className="text-primary hover:opacity-70 text-xs font-medium disabled:opacity-40 transition-opacity"
                          >
                            {testMutation.isPending && testMutation.variables === c.server_id
                              ? 'Testing…'
                              : 'Test'}
                          </button>
                          <button
                            onClick={() => openEdit(c)}
                            className="text-primary hover:opacity-70 text-xs font-medium transition-opacity"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => setConfirmDeleteId(c.server_id)}
                            disabled={unregisterMutation.isPending || confirmDeleteId === c.server_id}
                            className="text-destructive hover:opacity-70 text-xs font-medium disabled:opacity-40 transition-opacity"
                            aria-hidden={confirmDeleteId === c.server_id}
                          >
                            Remove
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Registration Modal ── */}
      {showModal && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4"
          data-testid="register-modal"
          onClick={(e) => { if (e.target === e.currentTarget) closeModal(); }}
        >
          <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
            {/* Modal header */}
            <div className="sticky top-0 bg-card border-b border-border px-6 py-4 rounded-t-2xl flex items-center justify-between">
              <div>
                <h2 className="text-lg font-semibold">
                  {editingId ? 'Edit Connector' : 'Register Connector'}
                </h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Connect an MCP server to your AgentVerse tenant
                </p>
              </div>
              <button
                onClick={closeModal}
                className="text-muted-foreground hover:text-foreground transition-colors text-xl leading-none"
                aria-label="Close"
              >
                ×
              </button>
            </div>

            <div className="px-6 py-5 space-y-5">
              {form.type_label && (
                <p className="text-xs text-muted-foreground">
                  Type: <span className="font-medium text-foreground">{form.type_label}</span>
                </p>
              )}
              {/* Name */}
              <div>
                <label htmlFor="connector-name" className="block text-sm font-medium mb-1">
                  Name <span className="text-destructive">*</span>
                </label>
                <input
                  id="connector-name"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="my-jira, github-org, slack-engineering…"
                  className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
                />
                {nameTaken ? (
                  <p role="alert" className="text-xs text-destructive mt-1">
                    A connector named “{form.name.trim()}” already exists — give this connection its own name.
                  </p>
                ) : (
                  <p className="text-xs text-muted-foreground mt-1">
                    A unique name for this connection — you can register several of the same type
                    (e.g. orders-db and analytics-db).
                  </p>
                )}
              </div>

              {/* URL — with connector-specific hint. A connection string has no
                  top-level URL: its URI is the masked field under Auth. */}
              {!isConnectionString && (
              <div>
                <label htmlFor="connector-url" className="block text-sm font-medium mb-1">
                  {urlConfig?.label ?? 'URL'} <span className="text-destructive">*</span>
                </label>
                <div className="relative">
                  <input
                    id="connector-url"
                    type="url"
                    value={form.url}
                    onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
                    placeholder={urlConfig?.url ?? 'https://api.example.com'}
                    className="w-full border border-input rounded-lg px-3 py-2 pr-9 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
                  />
                  {/* Only an http(s) URL is a link — never a DSN (it embeds a password). */}
                  {isHttpUrl(form.url) && (
                    <a
                      href={form.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-primary transition-colors"
                      aria-label="Open URL"
                    >
                      <ExternalLink className="h-4 w-4" />
                    </a>
                  )}
                </div>
                {urlConfig?.hint && (
                  <HintText id="url-hint" text={urlConfig.hint} />
                )}
                {/* Quick-fill buttons for known connectors */}
                {(form.connector_type || form.name.trim()).length > 2 && urlConfig && !form.url && (
                  <button
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, url: urlConfig.url }))}
                    className="mt-1.5 text-xs text-primary hover:opacity-80 underline-offset-2 hover:underline"
                  >
                    Use default: {urlConfig.url}
                  </button>
                )}
              </div>
              )}

              {/* Auth Type */}
              <AuthTypeSelector
                value={form.auth_type}
                onChange={requestAuthTypeChange}
              />

              {/* Auth Fields — type-aware, with the catalog entry's own fields */}
              {(form.auth_type !== 'none' || authFields.length > 0) && (
                <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-1">
                  <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${
                      AUTH_TYPE_CONFIGS[form.auth_type]?.color?.split(' ')[0] ?? 'bg-primary'
                    }`} />
                    {AUTH_TYPE_CONFIGS[form.auth_type]?.label ?? (form.auth_type || 'Authentication')}
                  </h3>
                  <SmartAuthFields
                    authType={form.auth_type}
                    fields={authFields}
                    authValues={form.auth_values}
                    connectorName={connectorNameForHints}
                    onChange={(values) => setForm((f) => ({ ...f, auth_values: values }))}
                  />
                </div>
              )}

              {/* Autonomous execution opt-in */}
              <label
                htmlFor="connector-auto-approve"
                className="flex items-start gap-3 rounded-xl border border-border bg-muted/20 p-4 cursor-pointer"
              >
                <input
                  id="connector-auto-approve"
                  type="checkbox"
                  checked={form.auto_approve}
                  onChange={(e) => setForm((f) => ({ ...f, auto_approve: e.target.checked }))}
                  className="mt-0.5 h-4 w-4 rounded border-input accent-primary"
                />
                <div className="space-y-0.5">
                  <span className="block text-sm font-medium">Allow autonomous execution</span>
                  <span className="block text-xs text-muted-foreground leading-relaxed">
                    Let agents run this connector's high-risk tools (e.g. send a message) without
                    waiting for human approval in autonomous goals. Only enable for connectors you
                    trust to act on your behalf — everything else still requires approval.
                  </span>
                </div>
              </label>

              {/* Error */}
              {formError && (
                <div
                  role="alert"
                  className="flex items-start gap-2 text-xs text-destructive bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg px-3 py-2"
                >
                  <XCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
                  {formError}
                </div>
              )}
            </div>

            {/* Modal footer */}
            <div className="sticky bottom-0 bg-card border-t border-border px-6 py-4 rounded-b-2xl flex gap-3 justify-end">
              <button
                onClick={closeModal}
                className="px-4 py-2 border border-border rounded-lg text-sm hover:bg-accent transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => registerMutation.mutate()}
                disabled={!canSubmit}
                className="bg-primary text-primary-foreground px-5 py-2 rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity flex items-center gap-2"
              >
                {registerMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {registerMutation.isPending
                  ? editingId ? 'Saving…' : 'Registering…'
                  : editingId ? 'Save Changes' : 'Register'}
              </button>
             </div>
           </div>
         </div>
       )}

      {/* ── Confirm auth-type switch that would drop entered values ── */}
      <ConfirmModal
        open={!!pendingAuthType}
        title="Switch auth type?"
        description={
          pendingAuthType
            ? `${AUTH_TYPE_CONFIGS[pendingAuthType.type]?.label ?? pendingAuthType.type} does not use: ${pendingAuthType.dropped.join(', ')}. Those values will be cleared.`
            : undefined
        }
        confirmLabel="Switch and clear"
        variant="warning"
        onConfirm={() => {
          if (pendingAuthType) applyAuthType(pendingAuthType.type, currentFieldKeys(pendingAuthType.type));
          setPendingAuthType(null);
        }}
        onCancel={() => setPendingAuthType(null)}
      />

      {/* ── Confirm Delete Modal ── */}
      <ConfirmModal
        open={!!confirmDeleteId}
        title="Remove connector?"
        description="This will permanently unregister the connector. Running goals that depend on it may fail."
        confirmLabel="Remove"
        variant="danger"
        isLoading={unregisterMutation.isPending}
        onConfirm={() => {
          if (confirmDeleteId) unregisterMutation.mutate(confirmDeleteId);
          setConfirmDeleteId(null);
        }}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </JARVISStagger>
    </JARVISPageShell>
  );
}
