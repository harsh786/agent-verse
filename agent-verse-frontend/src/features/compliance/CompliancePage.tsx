import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { complianceApi } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { toast } from "@/stores/toast";

const CONSENT_PURPOSES = ["analytics", "marketing", "ai_processing"] as const;
const TERMINAL_EXPORT = new Set(["complete", "completed", "failed"]);

export function CompliancePage() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [purpose, setPurpose] = useState<(typeof CONSENT_PURPOSES)[number]>("analytics");

  const holds = useQuery({ queryKey: ["legal-holds"], queryFn: () => complianceApi.listLegalHolds() });

  const exportStatus = useQuery({
    queryKey: ["gdpr-export", jobId],
    queryFn: () => complianceApi.getGdprExportStatus(jobId as string),
    enabled: jobId !== null,
    refetchInterval: (q) => {
      const s = (q.state.data as { status?: string } | undefined)?.status ?? "";
      return TERMINAL_EXPORT.has(s) ? false : 2000;
    },
  });

  const startExport = useMutation({
    mutationFn: () => complianceApi.startGdprExport(),
    onSuccess: (res) => {
      setJobId(res.job_id);
      toast({ kind: "info", message: "GDPR export started." });
    },
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const recordConsent = useMutation({
    mutationFn: () => complianceApi.recordConsent(purpose),
    onSuccess: () => toast({ kind: "success", message: `Consent recorded for ${purpose}.` }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });
  const revokeConsent = useMutation({
    mutationFn: () => complianceApi.revokeConsent(purpose),
    onSuccess: () => toast({ kind: "success", message: `Consent revoked for ${purpose}.` }),
    onError: (e) => toast({ kind: "error", message: String(e) }),
  });

  const job = exportStatus.data;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Compliance</h1>
        <p className="text-muted-foreground text-sm mt-1">Legal holds, data export, and consent.</p>
      </div>

      {/* Legal holds */}
      <section className="space-y-3">
        <h2 className="font-semibold">Legal holds</h2>
        {holds.isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (holds.data ?? []).length === 0 ? (
          <EmptyState title="No active legal holds" description="Data retention deletion runs normally." />
        ) : (
          <div className="space-y-2">
            {(holds.data ?? []).map((h) => (
              <div key={h.id} className="bg-card border border-border rounded-lg px-4 py-3 text-sm">
                <div className="font-medium">{h.reason}</div>
                <div className="text-xs text-muted-foreground mt-0.5">
                  by {h.created_by}{h.expires_at ? ` · expires ${h.expires_at}` : " · no expiry"}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* GDPR export */}
      <section className="space-y-3">
        <h2 className="font-semibold">GDPR data export</h2>
        <div className="bg-card border border-border rounded-xl p-4 space-y-3">
          <button
            onClick={() => startExport.mutate()}
            disabled={startExport.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Start GDPR export
          </button>
          {jobId && job && (
            <div className="flex items-center gap-3 text-sm">
              <span className="text-muted-foreground">Job {jobId.slice(0, 8)}:</span>
              <StatusBadge status={job.status} />
              {job.download_url && (
                <a href={job.download_url} className="text-primary underline" download>
                  Download
                </a>
              )}
              {job.error && <span className="text-destructive">{job.error}</span>}
            </div>
          )}
        </div>
      </section>

      {/* Consent */}
      <section className="space-y-3">
        <h2 className="font-semibold">Consent management</h2>
        <div className="flex flex-wrap gap-3 items-end bg-card border border-border rounded-xl p-4">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Purpose
            <select
              value={purpose}
              onChange={(e) => setPurpose(e.target.value as (typeof CONSENT_PURPOSES)[number])}
              className="px-2 py-1.5 border border-input rounded-md bg-background text-sm"
            >
              {CONSENT_PURPOSES.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <button
            onClick={() => recordConsent.mutate()}
            disabled={recordConsent.isPending}
            className="px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm hover:opacity-90 disabled:opacity-50"
          >
            Record consent
          </button>
          <button
            onClick={() => revokeConsent.mutate()}
            disabled={revokeConsent.isPending}
            className="px-4 py-2 bg-muted rounded-md text-sm hover:bg-accent disabled:opacity-50"
          >
            Revoke consent
          </button>
        </div>
      </section>
    </div>
  );
}

export default CompliancePage;
