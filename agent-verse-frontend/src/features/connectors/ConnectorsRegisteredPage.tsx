import { useState, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';
import { Eye, EyeOff, Plus, Trash2, ExternalLink, CheckCircle2, XCircle, Loader2, Info } from 'lucide-react';
import { connectorsApi, type ConnectorResponse } from '@/lib/api/client';

// ── Auth-type field definitions ─────────────────────────────────────────────

interface AuthField {
  key: string;
  label: string;
  placeholder: string;
  type: 'text' | 'password' | 'textarea' | 'email' | 'url';
  required: boolean;
  hint?: string;
}

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
    color: 'bg-gray-100 text-gray-800 dark:bg-gray-800/30 dark:text-gray-400',
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

// ── Connector-specific URL hints ────────────────────────────────────────────

const CONNECTOR_URL_MAP: Record<string, { url: string; hint: string; label: string }> = {
  jira:           { url: 'https://yourcompany.atlassian.net', hint: 'Replace "yourcompany" with your Atlassian subdomain', label: 'JIRA Base URL' },
  confluence:     { url: 'https://yourcompany.atlassian.net', hint: 'Same domain as JIRA for Atlassian Cloud', label: 'Confluence Base URL' },
  github:         { url: 'https://api.github.com', hint: 'For GitHub Enterprise: https://github.COMPANY.com/api/v3', label: 'GitHub API URL' },
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
    token: 'Generate at: github.com → Settings → Developer settings → Personal access tokens',
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
  authValues,
  connectorName,
  onChange,
}: {
  authType: string;
  authValues: Record<string, string>;
  connectorName: string;
  onChange: (values: Record<string, string>) => void;
}) {
  const config = AUTH_TYPE_CONFIGS[authType];
  if (!config) return null;

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
  if (!config.fields.length) {
    return (
      <div className="rounded-lg bg-muted/40 border border-border px-4 py-3 text-sm text-muted-foreground">
        No authentication required for this connector type.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {config.fields.map((field) => {
        const fieldHint = getFieldHint(connectorName, field.key, field.hint);
        const hintId = `hint-${field.key}`;
        const inputId = `auth-${field.key}`;
        return (
          <div key={field.key}>
            <label htmlFor={inputId} className="block text-sm font-medium mb-1">
              {field.label}
              {field.required && <span className="text-destructive ml-1">*</span>}
            </label>
            {field.type === 'password' ? (
              <PasswordInput
                id={inputId}
                value={authValues[field.key] ?? ''}
                onChange={(v) => setField(field.key, v)}
                placeholder={field.placeholder}
                aria-describedby={fieldHint ? hintId : undefined}
              />
            ) : field.type === 'textarea' ? (
              <textarea
                id={inputId}
                value={authValues[field.key] ?? ''}
                onChange={(e) => setField(field.key, e.target.value)}
                placeholder={field.placeholder}
                rows={4}
                aria-describedby={fieldHint ? hintId : undefined}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm font-mono bg-background focus:ring-2 focus:ring-primary outline-none resize-none"
              />
            ) : (
              <input
                id={inputId}
                type={field.type}
                value={authValues[field.key] ?? ''}
                onChange={(e) => setField(field.key, e.target.value)}
                placeholder={field.placeholder}
                aria-describedby={fieldHint ? hintId : undefined}
                className="w-full border border-input rounded-lg px-3 py-2 text-sm bg-background focus:ring-2 focus:ring-primary outline-none"
              />
            )}
            {fieldHint && <HintText id={hintId} text={fieldHint} />}
          </div>
        );
      })}
    </div>
  );
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
  url: string;
  auth_type: string;
  auth_values: Record<string, string>;
}

const EMPTY_FORM: FormState = {
  name: '',
  url: '',
  auth_type: 'bearer',
  auth_values: {},
};

function buildAuthConfig(_authType: string, authValues: Record<string, string>): Record<string, string> {
  // Strip empty values
  return Object.fromEntries(
    Object.entries(authValues).filter(([, v]) => v.trim() !== '')
  );
}

function parseAuthConfigToValues(_authType: string, authConfig: Record<string, string>): Record<string, string> {
  return { ...authConfig };
}

interface TestResult {
  reachable: boolean;
  status: string;
  latency_ms?: number;
  error?: string;
}

export function ConnectorsRegisteredPage() {
  const apiKey = useAuthStore((s) => s.apiKey);
  const qc = useQueryClient();
  const location = useLocation();
  const prefill = (location.state as any)?.prefill;

  const [showModal, setShowModal] = useState(!!prefill);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(() => {
    if (prefill) {
      return {
        name: prefill.name ?? '',
        url: prefill.url ?? prefill.default_url ?? '',
        auth_type: prefill.auth_type ?? 'bearer',
        auth_values: {},
      };
    }
    return EMPTY_FORM;
  });
  const [formError, setFormError] = useState('');
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

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
        url: form.url.trim(),
        auth_type: form.auth_type,
        auth_config,
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
    onSuccess: (data: any, id: string) =>
      setTestResults((prev) => ({ ...prev, [id]: data })),
  });

  const openCreate = useCallback(() => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError('');
    setShowModal(true);
  }, []);

  const openEdit = useCallback((c: ConnectorResponse) => {
    setEditingId(c.server_id);
    setForm({
      name: c.name,
      url: c.url,
      auth_type: (c as any).auth_type ?? 'bearer',
      auth_values: parseAuthConfigToValues((c as any).auth_type ?? 'bearer', (c as any).auth_config ?? {}),
    });
    setFormError('');
    setShowModal(true);
  }, []);

  const closeModal = useCallback(() => {
    setShowModal(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormError('');
  }, []);

  const urlConfig = getUrlConfig(form.name);

  // Validation
  const canSubmit =
    form.name.trim() &&
    form.url.trim() &&
    !registerMutation.isPending;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Registered Connectors</h1>
          <p className="text-muted-foreground text-sm mt-1">
            MCP servers connected to your tenant — {connectors.length} registered
          </p>
        </div>
        <button
          onClick={openCreate}
          className="bg-primary text-primary-foreground px-4 py-2 rounded-lg hover:opacity-90 text-sm font-medium transition-opacity"
        >
          + Register Connector
        </button>
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
                      <td className="px-4 py-3 font-medium">{c.name}</td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground max-w-xs truncate">
                        {c.url}
                      </td>
                      <td className="px-4 py-3">
                        {(c as any).auth_type && (
                          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                            AUTH_TYPE_CONFIGS[(c as any).auth_type]?.color ?? 'bg-muted text-muted-foreground'
                          }`}>
                            {AUTH_TYPE_CONFIGS[(c as any).auth_type]?.label ?? (c as any).auth_type}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {result ? (
                          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                            result.reachable
                              ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
                              : 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400'
                          }`}>
                            {result.reachable
                              ? <CheckCircle2 className="h-3 w-3" />
                              : <XCircle className="h-3 w-3" />}
                            {result.reachable ? `OK · ${result.latency_ms ?? '?'}ms` : result.status}
                          </span>
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
                            onClick={() => {
                              if (confirm(`Remove connector "${c.name}"?`)) {
                                unregisterMutation.mutate(c.server_id);
                              }
                            }}
                            disabled={unregisterMutation.isPending}
                            className="text-destructive hover:opacity-70 text-xs font-medium disabled:opacity-40 transition-opacity"
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
                <p className="text-xs text-muted-foreground mt-1">
                  A short unique name to identify this connector
                </p>
              </div>

              {/* URL — with connector-specific hint */}
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
                  {form.url && (
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
                {form.name.trim().length > 2 && urlConfig && !form.url && (
                  <button
                    type="button"
                    onClick={() => setForm((f) => ({ ...f, url: urlConfig.url }))}
                    className="mt-1.5 text-xs text-primary hover:opacity-80 underline-offset-2 hover:underline"
                  >
                    Use default: {urlConfig.url}
                  </button>
                )}
              </div>

              {/* Auth Type */}
              <AuthTypeSelector
                value={form.auth_type}
                onChange={(v) =>
                  setForm((f) => ({ ...f, auth_type: v, auth_values: {} }))
                }
              />

              {/* Smart Auth Fields */}
              {form.auth_type !== 'none' && (
                <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-1">
                  <h3 className="text-sm font-medium mb-3 flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${
                      AUTH_TYPE_CONFIGS[form.auth_type]?.color?.split(' ')[0] ?? 'bg-primary'
                    }`} />
                    {AUTH_TYPE_CONFIGS[form.auth_type]?.label ?? 'Authentication'}
                  </h3>
                  <SmartAuthFields
                    authType={form.auth_type}
                    authValues={form.auth_values}
                    connectorName={form.name}
                    onChange={(values) => setForm((f) => ({ ...f, auth_values: values }))}
                  />
                </div>
              )}

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
    </div>
  );
}
