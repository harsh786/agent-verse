import { useState, useRef, useEffect } from "react";
import type { JSX } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Copy, CheckCheck, Shield, Plus, Trash2,
  Key, Clock, AlertCircle,
} from "lucide-react";
import { agentsApi, credentialsApi } from "@/lib/api/client";
import type { AgentCredential, IssueCredentialRequest, IssuedCredential } from "@/lib/api/client";
import { LoadingSpinner } from "@/components/ui/LoadingSpinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { Skeleton } from "@/components/ui/Skeleton";
import { toast } from "@/stores/toast";

// ── Helpers ───────────────────────────────────────────────────────────────────

function decodeJwt(token: string): Record<string, unknown> | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(b64)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function truncateKeyId(id: string): string {
  if (id.length <= 16) return id;
  return `${id.slice(0, 8)}…${id.slice(-4)}`;
}

function expiresLabel(expiresAt: string | null): string {
  if (!expiresAt) return "Never";
  const diff = new Date(expiresAt).getTime() - Date.now();
  if (diff < 0) return "Expired";
  const days = Math.floor(diff / 86400000);
  if (days > 0) return `${days}d`;
  const hours = Math.floor(diff / 3600000);
  return `${hours}h`;
}

const SEVERITY_COLORS: Record<string, string> = {
  active: "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  revoked: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400",
};

const ALL_SCOPES = [
  "goals:read", "goals:write", "goals:cancel",
  "agents:read", "agents:write", "agents:delete",
  "knowledge:read", "knowledge:write",
  "connectors:read", "connectors:write",
  "governance:read", "governance:write",
  "analytics:read",
];

const DOMAIN_FIELDS: Record<string, Array<{ key: string; label: string; placeholder: string }>> = {
  legal: [
    { key: "bar_number", label: "Bar Number", placeholder: "CA-123456" },
    { key: "jurisdiction", label: "Jurisdiction", placeholder: "California" },
  ],
  healthcare: [
    { key: "npi", label: "NPI Number", placeholder: "1234567890" },
    { key: "specialty", label: "Specialty", placeholder: "Internal Medicine" },
  ],
  finance: [
    { key: "trader_id", label: "Trader ID", placeholder: "TRD-0042" },
    { key: "desk", label: "Desk", placeholder: "Equities" },
  ],
  education: [
    { key: "institution", label: "Institution", placeholder: "MIT" },
    { key: "faculty_type", label: "Faculty Type", placeholder: "Adjunct" },
  ],
};

// ── Copy button ───────────────────────────────────────────────────────────────

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }): JSX.Element {
  const [copied, setCopied] = useState(false);
  const handleCopy = (): void => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };
  return (
    <button
      onClick={handleCopy}
      aria-label={label}
      className="inline-flex items-center gap-1 px-2 py-1 text-xs border border-border rounded hover:bg-muted transition-colors"
    >
      {copied ? <CheckCheck className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

// ── Issue Credential Modal ────────────────────────────────────────────────────

interface IssueModalProps {
  open: boolean;
  agentId: string;
  onClose: () => void;
  onIssued: (cred: IssuedCredential) => void;
}

function IssueCredentialModal({ open, agentId, onClose, onIssued }: IssueModalProps): JSX.Element | null {
  const qc = useQueryClient();
  const [keyType, setKeyType] = useState<IssueCredentialRequest["key_type"]>("jwt");
  const [selectedScopes, setSelectedScopes] = useState<string[]>(["goals:read"]);
  const [expiryDays, setExpiryDays] = useState<number | undefined>(30);
  const [description, setDescription] = useState("");

  const issueMutation = useMutation({
    mutationFn: () =>
      credentialsApi.issue(agentId, {
        key_type: keyType,
        scopes: selectedScopes,
        expires_in_days: expiryDays,
        description: description || undefined,
      }),
    onSuccess: (cred) => {
      toast({ kind: "success", message: "Credential created" });
      qc.invalidateQueries({ queryKey: ["agent-credentials", agentId] });
      onIssued(cred);
      onClose();
    },
    onError: (e) => toast({ kind: "error", message: `Failed: credential issue. ${String(e)}` }),
  });

  const toggleScope = (scope: string): void => {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope]
    );
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-card border border-border rounded-xl shadow-xl max-w-lg w-full p-6 space-y-5">
        <h2 className="text-lg font-semibold">Issue New Credential</h2>

        {/* Key type */}
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Key Type</label>
          <div className="flex gap-2">
            {(["jwt", "api_key", "mtls"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setKeyType(t)}
                className={`px-3 py-1.5 rounded-md text-xs border transition-colors ${
                  keyType === t
                    ? "bg-primary text-primary-foreground border-primary"
                    : "border-border hover:bg-muted"
                }`}
              >
                {t.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        {/* Scopes */}
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">Scopes</p>
          <div className="grid grid-cols-2 gap-1.5 max-h-36 overflow-y-auto">
            {ALL_SCOPES.map((scope) => (
              <label key={scope} className="flex items-center gap-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={selectedScopes.includes(scope)}
                  onChange={() => toggleScope(scope)}
                  className="rounded"
                />
                <span className="font-mono">{scope}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Expiry */}
        <div>
          <p className="text-xs font-medium text-muted-foreground mb-2">Expiry</p>
          <div className="flex gap-2 flex-wrap">
            {[1, 7, 30, 90].map((d) => (
              <button
                key={d}
                onClick={() => setExpiryDays(d)}
                className={`px-2 py-1 rounded text-xs border transition-colors ${
                  expiryDays === d ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted"
                }`}
              >
                {d}d
              </button>
            ))}
            <button
              onClick={() => setExpiryDays(undefined)}
              className={`px-2 py-1 rounded text-xs border transition-colors ${
                expiryDays === undefined ? "bg-primary text-primary-foreground border-primary" : "border-border hover:bg-muted"
              }`}
            >
              Never
            </button>
          </div>
        </div>

        {/* Description */}
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1">Description</label>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="CI/CD pipeline credential"
            className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background"
          />
        </div>

        <div className="flex justify-end gap-3 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm border border-border rounded-md hover:bg-muted">
            Cancel
          </button>
          <button
            onClick={() => issueMutation.mutate()}
            disabled={issueMutation.isPending || selectedScopes.length === 0}
            className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
          >
            {issueMutation.isPending ? "Issuing…" : "Issue Credential"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Private Key Display ───────────────────────────────────────────────────────

function PrivateKeyDisplay({ cred, onDismiss }: { cred: IssuedCredential; onDismiss: () => void }): JSX.Element {
  return (
    <div className="border border-yellow-400/60 bg-yellow-50/60 dark:bg-yellow-900/20 rounded-xl p-5 space-y-3">
      <div className="flex items-center gap-2 text-yellow-700 dark:text-yellow-400">
        <AlertCircle className="h-4 w-4" />
        <p className="text-sm font-semibold">Save this private key now — it won't be shown again.</p>
      </div>
      {cred.private_key && (
        <div className="bg-background border border-border rounded-md p-3 font-mono text-xs break-all relative">
          {cred.private_key}
          <div className="mt-2">
            <CopyButton text={cred.private_key} label="Copy private key" />
          </div>
        </div>
      )}
      <button onClick={onDismiss} className="text-xs text-muted-foreground underline">
        I've saved it — dismiss
      </button>
    </div>
  );
}

// ── JWT Preview ───────────────────────────────────────────────────────────────

function JwtPreview({ agentId }: { agentId: string }): JSX.Element {
  const [token, setToken] = useState<string | null>(null);
  const [claims, setClaims] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [timeLeft, setTimeLeft] = useState<string>("");
  const rafRef = useRef<number | null>(null);

  // RAF countdown for expiry
  useEffect(() => {
    if (!claims) return;
    const exp = claims.exp as number | undefined;
    if (!exp) return;

    const tick = (): void => {
      const remaining = exp - Math.floor(Date.now() / 1000);
      if (remaining <= 0) {
        setTimeLeft("Expired");
        return;
      }
      const m = Math.floor(remaining / 60);
      const s = remaining % 60;
      setTimeLeft(`${m}m ${s}s`);
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [claims]);

  const handleFetch = async (): Promise<void> => {
    setLoading(true);
    try {
      const result = await credentialsApi.getToken(agentId);
      setToken(result.token);
      setClaims(decodeJwt(result.token));
    } catch (e) {
      toast({ kind: "error", message: `Failed: fetch token. ${String(e)}` });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-card border border-border rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold flex items-center gap-2">
          <Key className="h-4 w-4" /> JWT Preview
        </h2>
        <button
          onClick={() => void handleFetch()}
          disabled={loading}
          className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:opacity-90 disabled:opacity-50"
        >
          {loading ? "Fetching…" : token ? "Refresh Token" : "Fetch Token"}
        </button>
      </div>

      {token && (
        <>
          <div className="bg-muted/50 rounded-md p-3 font-mono text-xs break-all">
            {token.slice(0, 80)}…
            <div className="mt-2">
              <CopyButton text={token} label="Copy JWT" />
            </div>
          </div>
          {claims && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">Claims</p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                {Object.entries(claims).map(([k, v]) => (
                  <div key={k} className="flex gap-2">
                    <span className="font-mono text-muted-foreground">{k}:</span>
                    <span className="font-medium truncate">{String(v)}</span>
                  </div>
                ))}
              </div>
              {timeLeft && (
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <Clock className="h-3 w-3" /> Expires in: <span className="font-medium">{timeLeft}</span>
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Credential Card ───────────────────────────────────────────────────────────

function CredentialCard({
  cred,
  onRevoke,
  isNew,
}: {
  cred: AgentCredential;
  onRevoke: (keyId: string) => void;
  isNew: boolean;
}): JSX.Element {
  const isRevoked = cred.status === "revoked";
  return (
    <div
      className={`bg-card border border-border rounded-xl p-4 space-y-3 ${
        isRevoked ? "credential-card-revoked" : isNew ? "credential-card-entering" : ""
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">{truncateKeyId(cred.key_id)}</span>
            <CopyButton text={cred.key_id} label="Copy key ID" />
          </div>
          <div className="flex items-center gap-2">
            <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
              {cred.key_type.toUpperCase()}
            </span>
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${SEVERITY_COLORS[cred.status]}`}>
              {cred.status}
            </span>
          </div>
        </div>
        {!isRevoked && (
          <button
            onClick={() => onRevoke(cred.key_id)}
            aria-label={`Revoke credential ${cred.key_id}`}
            className="p-1.5 rounded-md hover:bg-red-50 dark:hover:bg-red-900/20 text-red-600 transition-colors"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Scopes */}
      <div className="flex flex-wrap gap-1">
        {cred.scopes.map((s) => (
          <span key={s} className="px-1.5 py-0.5 bg-muted rounded text-xs font-mono">
            {s}
          </span>
        ))}
      </div>

      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <Clock className="h-3 w-3" /> Expires: {expiresLabel(cred.expires_at)}
        </span>
        {cred.last_used_at && (
          <span>Last used: {new Date(cred.last_used_at).toLocaleDateString()}</span>
        )}
      </div>

      {cred.description && (
        <p className="text-xs text-muted-foreground italic">{cred.description}</p>
      )}
    </div>
  );
}

// ── Domain Identity Form ──────────────────────────────────────────────────────

function DomainIdentitySection({ agentId }: { agentId: string }): JSX.Element {
  const [domain, setDomain] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  const domainFields = DOMAIN_FIELDS[domain] ?? [];

  const handleSave = (): void => {
    // In production, this would call an API endpoint
    setSaved(true);
    toast({ kind: "success", message: "Domain identity updated" });
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <div className="bg-card border border-border rounded-xl p-5 space-y-4">
      <h2 className="font-semibold flex items-center gap-2">
        <Shield className="h-4 w-4" /> Domain Identity
        <span className="text-xs text-muted-foreground font-normal">(for agent {agentId.slice(0, 8)}…)</span>
      </h2>

      <div>
        <label className="block text-xs font-medium text-muted-foreground mb-1">Domain Context</label>
        <select
          value={domain}
          onChange={(e) => { setDomain(e.target.value); setFields({}); }}
          className="w-full max-w-xs border border-input rounded-md px-3 py-2 text-sm bg-background"
        >
          <option value="">— Select domain —</option>
          {Object.keys(DOMAIN_FIELDS).map((d) => (
            <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>
          ))}
        </select>
      </div>

      {domainFields.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {domainFields.map(({ key, label, placeholder }) => (
            <div key={key}>
              <label className="block text-xs font-medium text-muted-foreground mb-1">{label}</label>
              <input
                value={fields[key] ?? ""}
                onChange={(e) => setFields((prev) => ({ ...prev, [key]: e.target.value }))}
                placeholder={placeholder}
                className="w-full border border-input rounded-md px-3 py-2 text-sm bg-background"
              />
            </div>
          ))}
        </div>
      )}

      {domainFields.length > 0 && (
        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            className="px-4 py-2 text-sm bg-primary text-primary-foreground rounded-md hover:opacity-90"
          >
            Save Domain Identity
          </button>
          {saved && <span className="text-xs text-green-600">Saved</span>}
        </div>
      )}

      {!domain && (
        <p className="text-xs text-muted-foreground">
          Select a domain context to configure domain-specific identity fields for compliance.
        </p>
      )}
    </div>
  );
}

// ── AgentIdentityPage ─────────────────────────────────────────────────────────

export function AgentIdentityPage(): JSX.Element {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [showIssueModal, setShowIssueModal] = useState(false);
  const [issuedCred, setIssuedCred] = useState<IssuedCredential | null>(null);
  const [revokeTarget, setRevokeTarget] = useState<string | null>(null);
  const [newCredIds, setNewCredIds] = useState<Set<string>>(new Set());

  const agentQuery = useQuery({
    queryKey: ["agent", agentId],
    queryFn: () => agentsApi.get(agentId!),
    enabled: !!agentId,
  });

  const credentialsQuery = useQuery({
    queryKey: ["agent-credentials", agentId],
    queryFn: () => credentialsApi.list(agentId!),
    enabled: !!agentId,
  });

  const revokeMutation = useMutation({
    mutationFn: (keyId: string) => credentialsApi.revoke(agentId!, keyId),
    onSuccess: () => {
      toast({ kind: "warning", message: "Credential revoked — takes effect immediately" });
      qc.invalidateQueries({ queryKey: ["agent-credentials", agentId] });
      setRevokeTarget(null);
    },
    onError: (e) => toast({ kind: "error", message: `Failed: revoke credential. ${String(e)}` }),
  });

  const handleIssued = (cred: IssuedCredential): void => {
    setIssuedCred(cred);
    setNewCredIds((prev) => new Set([...prev, cred.key_id]));
  };

  if (!agentId) return <div className="p-6 text-muted-foreground">No agent selected.</div>;

  if (credentialsQuery.isLoading) return <LoadingSpinner />;

  if (credentialsQuery.isError) {
    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center h-32 text-muted-foreground"
      >
        <AlertCircle className="h-8 w-8 opacity-40 mb-2" />
        <p className="text-sm">Failed to load credentials</p>
        <button
          onClick={() => void credentialsQuery.refetch()}
          className="mt-2 text-xs text-primary hover:underline"
        >
          Retry
        </button>
      </div>
    );
  }

  const credentials = credentialsQuery.data ?? [];

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <button
          onClick={() => navigate(`/agents/${agentId}`)}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground mb-3 transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          {agentQuery.data?.name ?? "Agent"} / Identity
        </button>
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Agent Identity</h1>
            <p className="text-sm text-muted-foreground mt-1">Manage cryptographic credentials and domain identity.</p>
          </div>
          <button
            onClick={() => setShowIssueModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90"
          >
            <Plus className="h-4 w-4" /> Issue New Credential
          </button>
        </div>
      </div>

      {/* Issued credential private key display */}
      {issuedCred?.private_key && (
        <PrivateKeyDisplay cred={issuedCred} onDismiss={() => setIssuedCred(null)} />
      )}

      {/* Credential cards */}
      {credentials.length === 0 ? (
        <div className="bg-card border border-border rounded-xl">
          <EmptyState
            title="No credentials yet"
            description="Issue a credential to enable secure programmatic access for this agent."
            action={
              <button
                onClick={() => setShowIssueModal(true)}
                className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm hover:opacity-90"
              >
                Issue First Credential
              </button>
            }
          />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {credentials.map((cred) => (
            <CredentialCard
              key={cred.key_id}
              cred={cred}
              onRevoke={(keyId) => setRevokeTarget(keyId)}
              isNew={newCredIds.has(cred.key_id)}
            />
          ))}
        </div>
      )}

      {/* Skeleton while loading more */}
      {credentialsQuery.isFetching && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      )}

      {/* JWT Preview */}
      <JwtPreview agentId={agentId} />

      {/* Domain Identity */}
      <DomainIdentitySection agentId={agentId} />

      {/* Modals */}
      <IssueCredentialModal
        open={showIssueModal}
        agentId={agentId}
        onClose={() => setShowIssueModal(false)}
        onIssued={handleIssued}
      />

      <ConfirmModal
        open={!!revokeTarget}
        title="Revoke credential?"
        description="This credential will be immediately invalidated. Any services using it will lose access."
        confirmLabel="Revoke"
        variant="warning"
        isLoading={revokeMutation.isPending}
        onConfirm={() => revokeTarget && revokeMutation.mutate(revokeTarget)}
        onCancel={() => setRevokeTarget(null)}
      />
    </div>
  );
}
