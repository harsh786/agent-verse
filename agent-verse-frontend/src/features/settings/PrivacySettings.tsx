/**
 * PrivacySettings — AA5: GDPR self-service data controls.
 *
 * Surfaces:
 *   1. Right of Access (Art. 15)      — download all my data
 *   2. Right to Erasure (Art. 17)     — delete account + all data
 *   3. Right to Portability (Art. 20) — export machine-readable JSON
 *   4. Consent management             — analytics / marketing toggles
 *
 * Skills applied:
 *   frontend-design:   JARVIS dark, destructive-action danger palette
 *   emil-design-eng:   spring 600/35 toggle, 300/28 confirm modal
 *   impeccable-ui:     risk level hierarchy (info → warning → destructive)
 *   web-guidelines:    aria-live, role=switch, confirm dialog with aria
 *   ui-ux-pro-max:     44px targets, clear danger affordances, 3-step confirm
 */
import { useState, useCallback, useId } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Download, Trash2, Shield, ToggleLeft, ToggleRight, AlertTriangle, CheckCircle2, Loader2 } from 'lucide-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { apiRequest } from '@/lib/api/client';

const apiClient = {
  get: <T,>(path: string) => apiRequest<T>('GET', path),
  post: <T,>(path: string, body?: unknown) => apiRequest<T>('POST', path, body),
  put: <T,>(path: string, body?: unknown) => apiRequest<T>('PUT', path, body),
  delete: <T,>(path: string) => apiRequest<T>('DELETE', path),
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface ConsentSettings {
  analytics:  boolean;
  marketing:  boolean;
}

interface DataExportStatus {
  status:      'idle' | 'queued' | 'processing' | 'ready';
  downloadUrl?: string;
  sizeBytes?:  number;
  expiresAt?:  string;
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useConsentSettings() {
  return useQuery<ConsentSettings>({
    queryKey: ['consent-settings'],
    queryFn: () => apiClient.get<ConsentSettings>('/v1/account/consent').catch(() => ({ analytics: true, marketing: false })),
    staleTime: 300_000,
  });
}

function useExportStatus() {
  return useQuery<DataExportStatus>({
    queryKey: ['data-export-status'],
    queryFn: () => apiClient.get<DataExportStatus>('/v1/account/data-export').catch(() => ({ status: 'idle' })),
    refetchInterval: (q) => q.state.data?.status === 'processing' ? 5_000 : false,
  });
}

function useRequestExport() {
  return useMutation({
    mutationFn: () => apiClient.post<DataExportStatus>('/v1/account/data-export', {}),
  });
}

function useUpdateConsent() {
  return useMutation({
    mutationFn: (body: ConsentSettings) => apiClient.put('/v1/account/consent', body),
  });
}

function useRequestDeletion() {
  return useMutation({
    mutationFn: () => apiClient.post('/v1/account/delete-request', {}),
  });
}

// ── Spring constants ──────────────────────────────────────────────────────────

const SPRING_FAST  = { type: 'spring', stiffness: 600, damping: 35 } as const;
const SPRING_MODAL = { type: 'spring', stiffness: 300, damping: 28 } as const;

// ── Toggle component ──────────────────────────────────────────────────────────

function ConsentToggle({ id, label, desc, checked, onChange }: {
  id: string;
  label: string;
  desc: string;
  checked: boolean;
  onChange: (val: boolean) => void;
}) {
  const reduce = useReducedMotion();
  return (
    <div className="flex items-center justify-between py-4 border-b border-[#1E2535] last:border-0">
      <div className="flex-1 min-w-0 pr-4">
        <p className="text-[14px] font-medium text-[#F1F5F9]">{label}</p>
        <p className="text-[12px] text-[#64748B] mt-0.5">{desc}</p>
      </div>
      <motion.button
        role="switch"
        aria-checked={checked}
        aria-label={`${checked ? 'Disable' : 'Enable'} ${label}`}
        id={id}
        onClick={() => onChange(!checked)}
        whileTap={reduce ? {} : { scale: 0.9 }}
        transition={SPRING_FAST}
        style={{ touchAction: 'manipulation' }}
        className="flex-shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 rounded"
      >
        {checked
          ? <ToggleRight className="h-7 w-7 text-blue-400" aria-hidden />
          : <ToggleLeft className="h-7 w-7 text-[#475569]" aria-hidden />
        }
      </motion.button>
    </div>
  );
}

// ── Delete Confirm Modal ──────────────────────────────────────────────────────

function DeleteConfirmModal({ onConfirm, onClose }: { onConfirm: () => void; onClose: () => void }) {
  const titleId = useId();
  const reduce  = useReducedMotion();
  const [step, setStep] = useState<1 | 2 | 3>(1);

  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby={titleId}
      className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        className="absolute inset-0 bg-black/70" onClick={onClose} />
      <motion.div
        initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.93, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.93 }}
        transition={SPRING_MODAL}
        className="relative bg-[#0F1117] border border-red-500/30 rounded-2xl w-full max-w-md shadow-2xl"
      >
        <div className="p-6 text-center">
          <div className="w-12 h-12 rounded-full bg-red-500/10 flex items-center justify-center mx-auto mb-4">
            <AlertTriangle className="h-6 w-6 text-red-400" aria-hidden />
          </div>
          <h2 id={titleId} className="text-[18px] font-bold text-[#F1F5F9] mb-2">
            {step === 1 ? 'Delete Account?' : step === 2 ? 'Are you absolutely sure?' : 'Final confirmation'}
          </h2>
          <p className="text-[13px] text-[#94A3B8] mb-6 [text-wrap:balance]">
            {step === 1 && 'This will schedule deletion of your account and all associated data. You have 30 days to cancel.'}
            {step === 2 && 'All organizations, missions, agent configurations, and knowledge will be permanently deleted. This cannot be undone after 30 days.'}
            {step === 3 && 'A confirmation email will be sent. Your account will be fully deleted in 30 days unless you cancel.'}
          </p>
          <div className="flex gap-3">
            {step < 3 ? (
              <>
                <motion.button
                  whileTap={reduce ? {} : { scale: 0.97 }}
                  transition={SPRING_FAST}
                  onClick={() => setStep(s => (s + 1) as 2 | 3)}
                  style={{ touchAction: 'manipulation' }}
                  className="flex-1 py-2.5 rounded-xl bg-red-600 hover:bg-red-500 text-white text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 min-h-[44px]"
                >
                  {step === 1 ? 'Continue' : 'I understand, continue'}
                </motion.button>
                <motion.button
                  whileTap={reduce ? {} : { scale: 0.97 }}
                  transition={SPRING_FAST}
                  onClick={onClose}
                  style={{ touchAction: 'manipulation' }}
                  className="px-4 py-2.5 rounded-xl border border-[#2D3748] text-[#94A3B8] text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
                >
                  Cancel
                </motion.button>
              </>
            ) : (
              <>
                <motion.button
                  whileTap={reduce ? {} : { scale: 0.97 }}
                  transition={SPRING_FAST}
                  onClick={onConfirm}
                  style={{ touchAction: 'manipulation' }}
                  className="flex-1 py-2.5 rounded-xl bg-red-600 hover:bg-red-500 text-white text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 min-h-[44px]"
                >
                  Request Deletion
                </motion.button>
                <motion.button
                  whileTap={reduce ? {} : { scale: 0.97 }}
                  transition={SPRING_FAST}
                  onClick={onClose}
                  style={{ touchAction: 'manipulation' }}
                  className="px-4 py-2.5 rounded-xl border border-[#2D3748] text-[#94A3B8] text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 min-h-[44px]"
                >
                  Cancel
                </motion.button>
              </>
            )}
          </div>
        </div>
      </motion.div>
    </div>
  );
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} className="bg-[#1A1F2E] border border-[#2D3748] rounded-xl p-5 mb-4">
      <h2 className="text-[13px] font-semibold text-[#94A3B8] uppercase tracking-wider mb-4">{title}</h2>
      {children}
    </section>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export function PrivacySettings() {
  const reduce         = useReducedMotion();
  const { data: consent, refetch: refetchConsent } = useConsentSettings();
  const { data: exportStatus } = useExportStatus();
  const requestExport  = useRequestExport();
  const updateConsent  = useUpdateConsent();
  const requestDelete  = useRequestDeletion();
  const [showDelete, setShowDelete] = useState(false);
  const [deleted, setDeleted]       = useState(false);

  const handleConsentChange = useCallback(async (key: keyof ConsentSettings, val: boolean) => {
    await updateConsent.mutateAsync({ ...consent, [key]: val } as ConsentSettings);
    refetchConsent();
  }, [consent, updateConsent, refetchConsent]);

  const handleDelete = useCallback(async () => {
    await requestDelete.mutateAsync();
    setShowDelete(false);
    setDeleted(true);
  }, [requestDelete]);

  if (deleted) {
    return (
      <div className="max-w-xl mx-auto px-6 py-16 text-center">
        <CheckCircle2 className="h-12 w-12 text-emerald-400 mx-auto mb-4" aria-hidden />
        <h1 className="text-[20px] font-bold text-[#F1F5F9] mb-2">Deletion Requested</h1>
        <p className="text-[14px] text-[#94A3B8] [text-wrap:balance]">
          A confirmation email has been sent. Your account will be permanently deleted in 30 days.
          You can cancel by logging in before that date.
        </p>
      </div>
    );
  }

  return (
    <motion.div
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={SPRING_MODAL}
      className="max-w-2xl mx-auto px-6 py-8"
    >
      <h1 className="text-[24px] font-bold text-[#F1F5F9] [text-wrap:balance] mb-2">Privacy &amp; Data</h1>
      <p className="text-[14px] text-[#64748B] mb-8">Manage your personal data and privacy preferences.</p>

      {/* Your Data */}
      <Section title="Your Data">
        <div className="space-y-4">
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 rounded-xl bg-blue-500/10 flex items-center justify-center flex-shrink-0">
              <Download className="h-5 w-5 text-blue-400" aria-hidden />
            </div>
            <div className="flex-1">
              <p className="text-[14px] font-medium text-[#F1F5F9]">Download my data</p>
              <p className="text-[12px] text-[#64748B] mt-0.5">
                Export a complete JSON archive of your account, organisations, and missions.
                {exportStatus?.sizeBytes && ` Size: ~${Math.round(exportStatus.sizeBytes / 1024 / 1024)}MB`}
              </p>
              {exportStatus?.status === 'ready' && exportStatus.downloadUrl && (
                <a
                  href={exportStatus.downloadUrl}
                  download
                  className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-blue-400 hover:text-blue-300 underline-offset-2 hover:underline"
                >
                  <Download className="h-3.5 w-3.5" aria-hidden />
                  Download archive
                  {exportStatus.expiresAt && (
                    <span className="text-[#475569] no-underline">
                      (expires {new Intl.DateTimeFormat('en', { dateStyle: 'short' }).format(new Date(exportStatus.expiresAt))})
                    </span>
                  )}
                </a>
              )}
            </div>
            <motion.button
              whileTap={reduce ? {} : { scale: 0.97 }}
              transition={SPRING_FAST}
              onClick={() => requestExport.mutate()}
              disabled={requestExport.isPending || exportStatus?.status === 'processing' || exportStatus?.status === 'queued'}
              aria-label="Request data export"
              style={{ touchAction: 'manipulation' }}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-[#252B3B] hover:bg-[#2D3748] text-[13px] text-[#94A3B8] font-medium disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 min-h-[40px] flex-shrink-0"
            >
              {requestExport.isPending || exportStatus?.status === 'processing' ? (
                <><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />Processing…</>
              ) : exportStatus?.status === 'queued' ? (
                <>Queued</>
              ) : (
                <>Export</>
              )}
            </motion.button>
          </div>
        </div>
      </Section>

      {/* Consent */}
      <Section title="Consent &amp; Cookies">
        <ConsentToggle
          id="consent-analytics"
          label="Analytics cookies"
          desc="Help us understand how AgentVerse is used to improve the product."
          checked={consent?.analytics ?? true}
          onChange={val => handleConsentChange('analytics', val)}
        />
        <ConsentToggle
          id="consent-marketing"
          label="Marketing emails"
          desc="Product updates, tips, and occasional offers from the AgentVerse team."
          checked={consent?.marketing ?? false}
          onChange={val => handleConsentChange('marketing', val)}
        />
      </Section>

      {/* Danger zone */}
      <section aria-label="Danger zone" className="bg-[#1A1F2E] border border-red-500/20 rounded-xl p-5">
        <h2 className="text-[13px] font-semibold text-red-400 uppercase tracking-wider mb-4">Danger Zone</h2>
        <div className="flex items-start gap-4">
          <div className="w-10 h-10 rounded-xl bg-red-500/10 flex items-center justify-center flex-shrink-0">
            <Trash2 className="h-5 w-5 text-red-400" aria-hidden />
          </div>
          <div className="flex-1">
            <p className="text-[14px] font-medium text-[#F1F5F9]">Delete account</p>
            <p className="text-[12px] text-[#94A3B8] mt-0.5">
              Permanently deletes your account and all organisation data. Takes effect after 30 days.
              A confirmation email will be sent.
            </p>
          </div>
          <motion.button
            whileTap={reduce ? {} : { scale: 0.97 }}
            transition={SPRING_FAST}
            onClick={() => setShowDelete(true)}
            aria-label="Request account deletion"
            style={{ touchAction: 'manipulation' }}
            className="flex items-center gap-2 px-4 py-2 rounded-xl border border-red-500/30 text-[13px] text-red-400 hover:bg-red-500/10 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400/70 min-h-[40px] flex-shrink-0"
          >
            <Shield className="h-3.5 w-3.5" aria-hidden />
            Delete
          </motion.button>
        </div>
      </section>

      {/* Feedback */}
      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {updateConsent.isSuccess && 'Consent preferences saved.'}
        {requestExport.isSuccess && 'Data export queued. You will receive an email when ready.'}
      </div>

      {/* Delete confirm */}
      <AnimatePresence>
        {showDelete && (
          <DeleteConfirmModal onConfirm={handleDelete} onClose={() => setShowDelete(false)} />
        )}
      </AnimatePresence>
    </motion.div>
  );
}

export default PrivacySettings;
