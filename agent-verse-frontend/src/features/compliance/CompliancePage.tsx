import { useState } from "react";
import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Shield,
  FileText,
  Download,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Clock,
  FileCheck,
  Lock,
  Loader2,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import { complianceApi } from "@/lib/api/client";
import type { ComplianceFrameworkStatus, Contract } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { toast } from "@/stores/toast";

// ── Constants ──────────────────────────────────────────────────────────────────

const CONSENT_PURPOSES = ["analytics", "marketing", "ai_processing"] as const;
type ConsentPurpose = (typeof CONSENT_PURPOSES)[number];

const FRAMEWORKS = ["gdpr", "hipaa", "soc2"] as const;
type Framework = (typeof FRAMEWORKS)[number];

const TERMINAL_EXPORT = new Set(["complete", "completed", "failed"]);

const TABS = [
  { id: "frameworks" as const, label: "Frameworks", icon: Shield },
  { id: "holds" as const, label: "Legal Holds", icon: Lock },
  { id: "export" as const, label: "Data Export", icon: Download },
  { id: "contracts" as const, label: "Contracts", icon: FileText },
  { id: "consent" as const, label: "Consent", icon: FileCheck },
];
type TabId = (typeof TABS)[number]["id"];

const FRAMEWORK_LABEL: Record<Framework, string> = {
  gdpr: "GDPR",
  hipaa: "HIPAA",
  soc2: "SOC 2",
};

// ── Shared: Modal ──────────────────────────────────────────────────────────────

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-5">
          <h3 className="font-semibold text-base">{title}</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-muted-foreground hover:text-foreground transition-colors rounded-md p-0.5"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ── Framework Status Card ──────────────────────────────────────────────────────

function FrameworkCard({
  framework,
  data,
  isLoading,
  isRunning,
  onRerun,
}: {
  framework: Framework;
  data: ComplianceFrameworkStatus | undefined;
  isLoading: boolean;
  isRunning: boolean;
  onRerun: () => void;
}) {
  return (
    <div className="bg-card border border-border rounded-xl p-5 flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-muted-foreground" />
          <span className="font-semibold text-sm">{FRAMEWORK_LABEL[framework]}</span>
        </div>
        {isLoading || isRunning ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : data ? (
          data.compliant ? (
            <CheckCircle className="h-5 w-5 text-green-500" />
          ) : (
            <XCircle className="h-5 w-5 text-red-500" />
          )
        ) : (
          <AlertTriangle className="h-5 w-5 text-amber-500" />
        )}
      </div>

      {/* Compliance badge */}
      {!isLoading && data && (
        <span
          className={`self-start inline-flex items-center gap-1 text-xs font-medium px-2.5 py-0.5 rounded-full ${
            data.compliant
              ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300"
              : "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300"
          }`}
        >
          {data.compliant ? (
            <CheckCircle className="h-3 w-3" />
          ) : (
            <XCircle className="h-3 w-3" />
          )}
          {data.compliant ? "Compliant" : "Non-compliant"}
        </span>
      )}

      {/* Checks list */}
      <div className="flex-1 min-h-[80px]">
        {isLoading ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-3.5 w-full" />
            ))}
          </div>
        ) : data?.checks?.length ? (
          <ul className="space-y-1.5">
            {data.checks.map((c, i) => (
              <li key={i} className="flex items-start gap-2 text-xs">
                {c.passed ? (
                  <CheckCircle className="h-3.5 w-3.5 text-green-500 shrink-0 mt-0.5" />
                ) : (
                  <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0 mt-0.5" />
                )}
                <span className={c.passed ? "" : "text-red-600 dark:text-red-400"}>
                  {c.check}
                  {c.detail && (
                    <span className="text-muted-foreground ml-1">— {c.detail}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">No check data available.</p>
        )}
      </div>

      {/* Re-run button */}
      <button
        onClick={onRerun}
        disabled={isRunning}
        className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground hover:text-foreground border-t border-border pt-3 transition-colors disabled:opacity-50 w-full"
      >
        <RefreshCw className={`h-3 w-3 ${isRunning ? "animate-spin" : ""}`} />
        Re-run check
      </button>
    </div>
  );
}

// ── Tab: Frameworks ────────────────────────────────────────────────────────────

function FrameworksTab() {
  const qc = useQueryClient();
  const [runningFw, setRunningFw] = useState<Framework | null>(null);

  const gdpr = useQuery({
    queryKey: ["compliance", "gdpr"],
    queryFn: () => complianceApi.getFrameworkStatus("gdpr"),
  });
  const hipaa = useQuery({
    queryKey: ["compliance", "hipaa"],
    queryFn: () => complianceApi.getFrameworkStatus("hipaa"),
  });
  const soc2 = useQuery({
    queryKey: ["compliance", "soc2"],
    queryFn: () => complianceApi.getFrameworkStatus("soc2"),
  });

  const runCheck = useMutation({
    mutationFn: (fw: Framework) => complianceApi.runComplianceCheck(fw),
    onMutate: (fw) => setRunningFw(fw),
    onSettled: () => setRunningFw(null),
    onSuccess: (data, fw) => {
      qc.setQueryData(["compliance", fw], data);
      toast({ kind: "success", message: `${FRAMEWORK_LABEL[fw]} check complete.` });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const queries = { gdpr, hipaa, soc2 };

  return (
    <div data-testid="framework-status" className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {FRAMEWORKS.map((fw) => (
        <FrameworkCard
          key={fw}
          framework={fw}
          data={queries[fw].data}
          isLoading={queries[fw].isLoading}
          isRunning={runningFw === fw}
          onRerun={() => runCheck.mutate(fw)}
        />
      ))}
    </div>
  );
}

// ── Tab: Legal Holds ───────────────────────────────────────────────────────────

function LegalHoldsTab() {
  const qc = useQueryClient();
  const [showModal, setShowModal] = useState(false);
  const [reason, setReason] = useState("");
  const [expiresAt, setExpiresAt] = useState("");

  const holds = useQuery({
    queryKey: ["legal-holds"],
    queryFn: () => complianceApi.listLegalHolds(),
  });

  const createHold = useMutation({
    mutationFn: () =>
      complianceApi.createLegalHold(reason.trim(), expiresAt || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["legal-holds"] });
      toast({ kind: "success", message: "Legal hold placed successfully." });
      setShowModal(false);
      setReason("");
      setExpiresAt("");
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  return (
    <section data-testid="legal-holds-section" className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold text-sm">Legal Holds</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Prevent data deletion for litigation, audit, or regulatory purposes.
          </p>
        </div>
        <button
          data-testid="place-hold-btn"
          onClick={() => setShowModal(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-lg text-xs font-medium hover:opacity-90 transition-opacity shrink-0"
        >
          <Plus className="h-3.5 w-3.5" />
          Place Hold
        </button>
      </div>

      {holds.isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[60px] w-full rounded-lg" />
          ))}
        </div>
      ) : holds.isError ? (
        <div className="flex items-center gap-2 text-sm text-destructive bg-destructive/10 rounded-lg p-4">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Failed to load legal holds.
        </div>
      ) : (holds.data ?? []).length === 0 ? (
        <EmptyState
          title="No active legal holds"
          description="Data deletion runs on the normal schedule. Place a hold to freeze data for compliance or litigation."
          action={
            <button
              onClick={() => setShowModal(true)}
              className="text-xs text-primary hover:underline"
            >
              Place your first hold
            </button>
          }
        />
      ) : (
        <ul className="space-y-2">
          {(holds.data ?? []).map((h) => (
            <li
              key={h.id}
              className="bg-card border border-border rounded-lg px-4 py-3 flex items-start justify-between gap-4"
            >
              <div className="space-y-0.5 min-w-0">
                <div className="flex items-center gap-2">
                  <Lock className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                  <span className="font-medium text-sm truncate">{h.reason}</span>
                </div>
                <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground pl-5">
                  <span>by {h.created_by}</span>
                  {h.expires_at ? (
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      expires {new Date(h.expires_at).toLocaleDateString()}
                    </span>
                  ) : (
                    <span className="text-amber-600 dark:text-amber-400 font-medium">
                      no expiry
                    </span>
                  )}
                </div>
              </div>
              <StatusBadge status="active" />
            </li>
          ))}
        </ul>
      )}

      {showModal && (
        <Modal title="Place Legal Hold" onClose={() => setShowModal(false)}>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium">
                Reason <span className="text-destructive">*</span>
              </label>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Describe the legal or compliance reason for this hold…"
                rows={3}
                className="w-full px-3 py-2 border border-input rounded-lg bg-background text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring resize-none"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium">
                Expires at{" "}
                <span className="text-muted-foreground font-normal">(optional)</span>
              </label>
              <input
                type="datetime-local"
                value={expiresAt}
                onChange={(e) => setExpiresAt(e.target.value)}
                className="w-full px-3 py-2 border border-input rounded-lg bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div className="flex gap-3 pt-1">
              <button
                onClick={() => createHold.mutate()}
                disabled={!reason.trim() || createHold.isPending}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {createHold.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                Place Hold
              </button>
              <button
                onClick={() => setShowModal(false)}
                className="px-4 py-2 bg-muted text-muted-foreground rounded-lg text-sm hover:bg-accent transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}

// ── Tab: Data Export ───────────────────────────────────────────────────────────

function DataExportTab() {
  const [jobId, setJobId] = useState<string | null>(null);

  const exportStatus = useQuery({
    queryKey: ["gdpr-export", jobId],
    queryFn: () => complianceApi.getGdprExportStatus(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (q) => {
      const status =
        (q.state.data as { status?: string } | undefined)?.status ?? "";
      return TERMINAL_EXPORT.has(status) ? false : 2000;
    },
  });

  const startExport = useMutation({
    mutationFn: () => complianceApi.startGdprExport(),
    onSuccess: (res) => {
      setJobId(res.job_id);
      toast({ kind: "info", message: "GDPR export job started." });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const job = exportStatus.data;
  const isTerminal = job ? TERMINAL_EXPORT.has(job.status) : false;
  const isInProgress = !!jobId && !!job && !isTerminal;

  return (
    <section data-testid="gdpr-export-section" className="space-y-4">
      <div>
        <h2 className="font-semibold text-sm">GDPR Data Export</h2>
        <p className="text-xs text-muted-foreground mt-0.5">
          Export all personal data associated with this tenant as a portable archive.
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl p-5 space-y-5">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Download className="h-4 w-4 text-muted-foreground" />
              <span className="font-medium text-sm">Tenant Data Archive</span>
            </div>
            <p className="text-xs text-muted-foreground max-w-sm">
              Includes goals, agents, audit logs, consent records, and all stored
              personal data per GDPR Art. 20.
            </p>
          </div>
          <button
            data-testid="start-export-btn"
            onClick={() => startExport.mutate()}
            disabled={startExport.isPending || isInProgress}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity shrink-0"
          >
            {startExport.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Starting…
              </>
            ) : (
              <>
                <Download className="h-4 w-4" />
                Start Export
              </>
            )}
          </button>
        </div>

        {jobId && (
          <div className="border-t border-border pt-4 space-y-3">
            {/* Job ID + status */}
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-xs text-muted-foreground font-mono bg-muted px-2 py-0.5 rounded">
                Job {jobId.slice(0, 12)}…
              </span>
              {job && <StatusBadge status={job.status} pulse={isInProgress} />}
              {exportStatus.isLoading && (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
              )}
            </div>

            {/* Running state */}
            {isInProgress && (
              <div className="flex items-center gap-2.5 text-xs text-muted-foreground bg-muted/60 rounded-lg px-3 py-2.5">
                <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
                Processing your data export… polling every 2 s.
              </div>
            )}

            {/* Error state */}
            {job?.status === "failed" && job.error && (
              <div className="flex items-start gap-2 text-xs text-destructive bg-destructive/10 border border-destructive/20 rounded-lg px-3 py-2.5">
                <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                <span>{job.error}</span>
              </div>
            )}

            {/* Success state */}
            {(job?.status === "complete" || job?.status === "completed") && (
              <div className="space-y-2">
                {job.download_url && (
                  <a
                    href={job.download_url}
                    download
                    className="inline-flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium transition-colors"
                  >
                    <Download className="h-4 w-4" />
                    Download Archive
                  </a>
                )}
                {job.completed_at && (
                  <p className="text-xs text-muted-foreground">
                    Completed {new Date(job.completed_at).toLocaleString()}
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

// ── Tab: Contracts ─────────────────────────────────────────────────────────────

function ContractsTab() {
  const qc = useQueryClient();
  const [signingContract, setSigningContract] = useState<Contract | null>(null);
  const [signerName, setSignerName] = useState("");
  const [signerEmail, setSignerEmail] = useState("");

  const contracts = useQuery({
    queryKey: ["contracts"],
    queryFn: () => complianceApi.listContracts(),
  });

  const signContract = useMutation({
    mutationFn: () =>
      complianceApi.signContract(
        signingContract!.contract_type,
        signerName.trim(),
        signerEmail.trim()
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contracts"] });
      toast({
        kind: "success",
        message: `${signingContract?.contract_type.replace(/_/g, " ")} contract signed.`,
      });
      setSigningContract(null);
      setSignerName("");
      setSignerEmail("");
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const contractList = contracts.data ?? [];

  return (
    <section className="space-y-4">
      <div>
        <h2 className="font-semibold text-sm">Contracts</h2>
        <p className="text-xs text-muted-foreground mt-0.5">
          Review and sign data processing and usage agreements.
        </p>
      </div>

      {contracts.isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-[60px] w-full rounded-lg" />
          ))}
        </div>
      ) : contracts.isError ? (
        <div className="flex items-center gap-2 text-sm text-destructive bg-destructive/10 rounded-lg p-4">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          Failed to load contracts.
        </div>
      ) : contractList.length === 0 ? (
        <EmptyState
          title="No contracts available"
          description="Data processing and service agreements will appear here once issued."
        />
      ) : (
        <ul className="space-y-2">
          {contractList.map((c) => (
            <li
              key={c.contract_id ?? c.contract_type}
              className="bg-card border border-border rounded-lg px-4 py-3 flex items-center justify-between gap-4"
            >
              <div className="flex items-center gap-3 min-w-0">
                <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="min-w-0">
                  <p className="font-medium text-sm capitalize">
                    {c.contract_type.replace(/_/g, " ")}
                  </p>
                  {c.signed_by && (
                    <p className="text-xs text-muted-foreground truncate">
                      Signed by {c.signed_by}
                      {c.signed_at
                        ? ` · ${new Date(c.signed_at).toLocaleDateString()}`
                        : ""}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <StatusBadge status={c.status} />
                {c.status === "pending_signature" && (
                  <button
                    onClick={() => setSigningContract(c)}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-lg text-xs font-medium hover:opacity-90 transition-opacity"
                  >
                    <FileCheck className="h-3.5 w-3.5" />
                    Sign
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {signingContract && (
        <Modal
          title={`Sign: ${signingContract.contract_type.replace(/_/g, " ")}`}
          onClose={() => setSigningContract(null)}
        >
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium">
                Signer name <span className="text-destructive">*</span>
              </label>
              <input
                value={signerName}
                onChange={(e) => setSignerName(e.target.value)}
                placeholder="Jane Smith"
                className="w-full px-3 py-2 border border-input rounded-lg bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium">
                Signer email <span className="text-destructive">*</span>
              </label>
              <input
                type="email"
                value={signerEmail}
                onChange={(e) => setSignerEmail(e.target.value)}
                placeholder="jane@company.com"
                className="w-full px-3 py-2 border border-input rounded-lg bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div className="flex gap-3 pt-1">
              <button
                onClick={() => signContract.mutate()}
                disabled={
                  !signerName.trim() ||
                  !signerEmail.trim() ||
                  signContract.isPending
                }
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
              >
                {signContract.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                Sign Contract
              </button>
              <button
                onClick={() => setSigningContract(null)}
                className="px-4 py-2 bg-muted text-muted-foreground rounded-lg text-sm hover:bg-accent transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}

// ── Tab: Consent ───────────────────────────────────────────────────────────────

const LEGAL_BASES = [
  { value: "legitimate_interest", label: "Legitimate interest" },
  { value: "consent", label: "Explicit consent" },
  { value: "contract", label: "Contractual necessity" },
  { value: "legal_obligation", label: "Legal obligation" },
] as const;

function ConsentTab() {
  const [purpose, setPurpose] = useState<ConsentPurpose>("analytics");
  const [legalBasis, setLegalBasis] = useState("legitimate_interest");

  const recordConsent = useMutation({
    mutationFn: () => complianceApi.recordConsent(purpose, legalBasis),
    onSuccess: () =>
      toast({ kind: "success", message: `Consent recorded for "${purpose}".` }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const revokeConsent = useMutation({
    mutationFn: () => complianceApi.revokeConsent(purpose),
    onSuccess: () =>
      toast({ kind: "success", message: `Consent revoked for "${purpose}".` }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const purposeLabel = purpose.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const basisLabel =
    LEGAL_BASES.find((b) => b.value === legalBasis)?.label ?? legalBasis;

  return (
    <section data-testid="consent-section" className="space-y-4">
      <div>
        <h2 className="font-semibold text-sm">Consent Management</h2>
        <p className="text-xs text-muted-foreground mt-0.5">
          Record or revoke data-processing consent per purpose under GDPR Art. 6.
        </p>
      </div>

      <div className="bg-card border border-border rounded-xl p-5 space-y-5">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-xs font-medium">Purpose</label>
            <select
              value={purpose}
              onChange={(e) => setPurpose(e.target.value as ConsentPurpose)}
              className="w-full px-3 py-2 border border-input rounded-lg bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {CONSENT_PURPOSES.map((p) => (
                <option key={p} value={p}>
                  {p.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium">Legal basis</label>
            <select
              value={legalBasis}
              onChange={(e) => setLegalBasis(e.target.value)}
              className="w-full px-3 py-2 border border-input rounded-lg bg-background text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {LEGAL_BASES.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Preview */}
        <div className="bg-muted/40 border border-border rounded-lg px-4 py-3 space-y-0.5">
          <p className="text-xs text-muted-foreground">Selected</p>
          <p className="font-medium text-sm">{purposeLabel}</p>
          <p className="text-xs text-muted-foreground">{basisLabel}</p>
        </div>

        <div className="flex flex-wrap gap-3">
          <button
            onClick={() => recordConsent.mutate()}
            disabled={recordConsent.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {recordConsent.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <CheckCircle className="h-4 w-4" />
            )}
            Record consent
          </button>
          <button
            onClick={() => revokeConsent.mutate()}
            disabled={revokeConsent.isPending}
            className="flex items-center gap-2 px-4 py-2 bg-muted text-muted-foreground rounded-lg text-sm font-medium hover:bg-accent disabled:opacity-50 transition-colors"
          >
            {revokeConsent.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <XCircle className="h-4 w-4" />
            )}
            Revoke consent
          </button>
        </div>
      </div>
    </section>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export function CompliancePage() {
  const [activeTab, setActiveTab] = useState<TabId>("frameworks");

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <div className="p-2 bg-primary/10 rounded-lg shrink-0">
          <Shield className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-tight">Compliance</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Frameworks, legal holds, data exports, contracts, and consent.
          </p>
        </div>
      </div>

      {/* Tab navigation */}
      <nav className="flex items-center gap-0.5 border-b border-border overflow-x-auto -mb-px">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveTab(id)}
            className={`flex items-center gap-1.5 px-3.5 py-2.5 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === id
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground/30"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </nav>

      {/* Tab panels */}
      <div>
        {activeTab === "frameworks" && <FrameworksTab />}
        {activeTab === "holds" && <LegalHoldsTab />}
        {activeTab === "export" && <DataExportTab />}
        {activeTab === "contracts" && <ContractsTab />}
        {activeTab === "consent" && <ConsentTab />}
      </div>
    </div>
  );
}

export default CompliancePage;
