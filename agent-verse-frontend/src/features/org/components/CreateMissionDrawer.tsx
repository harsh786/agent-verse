/**
 * CreateMissionDrawer — JARVIS-style slide-up drawer to create a mission.
 *
 * Skills:
 *   - emil-design-eng: spring slide-in (fluid 300/25), exit shorter than enter
 *   - impeccable-ui:   form hierarchy, actionable labels, error inline
 *   - web-guidelines:  form labels, aria-required, aria-invalid, touch-action
 *   - ui-ux-pro-max:   44px submit, useReducedMotion, focus trap
 */
import { useCallback, useId, useRef, useEffect, useState } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X, Zap, Paperclip, FileText, Loader2 } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { cn } from '@/lib/utils';
import { useCreateMission } from '../hooks/useOrg';
import { orgApi } from '../api';
import type { CreateMissionRequest } from '../types';

interface CreateMissionDrawerProps {
  orgId: string;
  open:  boolean;
  onClose: () => void;
}

// Spring configs (emil-design-eng)
const DRAWER_SPRING  = { type: 'spring', stiffness: 300, damping: 28 } as const;
const OVERLAY_SPRING = { type: 'spring', stiffness: 400, damping: 35 } as const;

export function CreateMissionDrawer({ orgId, open, onClose }: CreateMissionDrawerProps) {
  const reduce = useReducedMotion();
  const createMission = useCreateMission(orgId);
  const formId = useId();
  const firstInputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [uploadError, setUploadError] = useState('');
  const [busy, setBusy] = useState(false);

  const { register, handleSubmit, reset, formState: { errors, isSubmitting } } =
    useForm<CreateMissionRequest>({
      defaultValues: { org_id: orgId, priority: 'medium' },
    });

  // Focus first input when drawer opens (a11y)
  useEffect(() => {
    if (open) setTimeout(() => firstInputRef.current?.focus(), 100);
  }, [open]);

  // Close on Escape (web-guidelines)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && open) onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const onSubmit = useCallback(async (data: CreateMissionRequest) => {
    setUploadError('');
    setBusy(true);
    try {
      // Upload any attachments first, then tell the agent where to find them.
      // The extract_document OCR/vision tool reads these server paths at run time.
      let objective = data.objective ?? '';
      if (files.length > 0) {
        const uploaded = await Promise.all(files.map((f) => orgApi.uploadAttachment(orgId, f)));
        const lines = uploaded.map((u) => `- ${u.filename} → ${u.path}`).join('\n');
        objective =
          `${objective}\n\nAttached files (use the extract_document tool to read them ` +
          `when the task needs their contents):\n${lines}`.trim();
      }
      // Fire-and-forget: the backend forms the team + dispatches (30-90s), but
      // the mission shows up in the list immediately via the mission.created SSE
      // event, so we don't block the drawer on that slow response. Close now;
      // the list and the Live Agent Network update live as the mission runs.
      createMission.mutate({ ...data, objective });
      reset();
      setFiles([]);
      onClose();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Could not upload attachment.');
    } finally {
      setBusy(false);
    }
  }, [createMission, reset, onClose, files, orgId]);

  const addFiles = useCallback((incoming: FileList | null) => {
    if (!incoming) return;
    setUploadError('');
    setFiles((prev) => [...prev, ...Array.from(incoming)].slice(0, 5));
  }, []);

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={reduce ? { duration: 0 } : OVERLAY_SPRING}
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
            onClick={onClose}
            aria-hidden
          />

          {/* Drawer (slides up from bottom — Emil: panels spring fluid) */}
          <motion.div
            key="drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Create new mission"
            initial={{ y: reduce ? 0 : '100%', opacity: reduce ? 0 : 1 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: reduce ? 0 : '60%', opacity: 0 }}  // exit shorter (Emil rule)
            transition={reduce ? { duration: 0.15 } : DRAWER_SPRING}
            // web-guidelines: overscroll-behavior:contain in drawers
            style={{ overscroll: 'contain' } as React.CSSProperties}
            className={cn(
              // Mobile: full-width bottom sheet. Desktop: a centered, constrained
              // floating card lifted off the bottom edge — never edge-to-edge.
              'fixed inset-x-0 bottom-0 z-50 mx-auto w-full max-w-lg',
              'max-h-[90dvh] overflow-y-auto',
              'bg-[#1A1F2E] border border-[#2D3748]',
              'rounded-t-2xl sm:mb-6 sm:rounded-2xl',
              'shadow-[0_-20px_60px_rgba(0,0,0,0.6)] sm:shadow-[0_24px_80px_rgba(0,0,0,0.65)]',
            )}
          >
            {/* Drag handle — a mobile bottom-sheet affordance; hidden on desktop */}
            <div className="flex justify-center pt-3 pb-1 sm:hidden">
              <div className="w-10 h-1 rounded-full bg-[#3D4A5C]" aria-hidden />
            </div>

            {/* Header */}
            <div className="flex items-center justify-between px-6 py-3 border-b border-[#2D3748]">
              <div className="flex items-center gap-2">
                <Zap className="h-4 w-4 text-blue-400" aria-hidden />
                {/* impeccable-ui: heading is ONE dominant element */}
                <h2 className="text-[15px] font-semibold text-[#F1F5F9] tracking-[-0.01em]">
                  New Mission
                </h2>
              </div>
              <button
                onClick={onClose}
                aria-label="Close drawer"
                style={{ touchAction: 'manipulation' }}
                className={cn(
                  'p-2 rounded-lg text-[#94A3B8]',
                  'hover:text-[#F1F5F9] hover:bg-[#252B3B]',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60',
                  'transition-colors duration-150',
                  // ui-ux-pro-max: 44px minimum touch target
                  'min-w-[44px] min-h-[44px] flex items-center justify-center',
                )}
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>

            {/* Form */}
            <form
              id={formId}
              onSubmit={handleSubmit(onSubmit)}
              className="px-6 py-5 space-y-5"
            >
              {/* Title (impeccable-ui: primary field, visually largest) */}
              <div className="space-y-1.5">
                <label
                  htmlFor={`${formId}-title`}
                  className="block text-sm font-medium text-[#F1F5F9]"
                >
                  Mission title <span className="text-rose-400" aria-hidden>*</span>
                </label>
                <input
                  id={`${formId}-title`}
                  type="text"
                  autoComplete="off"
                  aria-required="true"
                  aria-invalid={!!errors.title}
                  aria-describedby={errors.title ? `${formId}-title-err` : undefined}
                  placeholder="e.g. Research AI market trends…"
                  {...register('title', { required: 'Title is required', minLength: { value: 3, message: 'At least 3 characters' } })}
                  ref={(el) => {
                    register('title').ref(el);
                    (firstInputRef as React.MutableRefObject<HTMLInputElement | null>).current = el;
                  }}
                  className={cn(
                    'w-full px-3 py-2.5 rounded-lg text-[15px]',
                    'bg-[#252B3B] border text-[#F1F5F9]',
                    'placeholder:text-[#475569]',
                    'focus:outline-none focus:ring-2 focus:ring-blue-500/60',
                    errors.title ? 'border-rose-500/60' : 'border-[#2D3748] focus:border-blue-500/40',
                    'transition-colors duration-150',
                  )}
                />
                {errors.title && (
                  <p id={`${formId}-title-err`} role="alert" className="text-xs text-rose-400">
                    {errors.title.message}
                  </p>
                )}
              </div>

              {/* Objective */}
              <div className="space-y-1.5">
                <label htmlFor={`${formId}-obj`} className="block text-sm font-medium text-[#F1F5F9]">
                  Objective
                </label>
                <textarea
                  id={`${formId}-obj`}
                  rows={3}
                  placeholder="What should the agents accomplish?"
                  {...register('objective')}
                  className={cn(
                    'w-full px-3 py-2.5 rounded-lg text-[14px] leading-relaxed',
                    'bg-[#252B3B] border border-[#2D3748] text-[#F1F5F9]',
                    'placeholder:text-[#475569] resize-none',
                    'focus:outline-none focus:ring-2 focus:ring-blue-500/60 focus:border-blue-500/40',
                    'transition-colors duration-150',
                  )}
                />
              </div>

              {/* Attachments — files the agent can OCR / vision-process at run time */}
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-[#F1F5F9]">
                  Attachments <span className="text-[#64748B] font-normal">(optional)</span>
                </label>
                <label
                  className={cn(
                    'flex items-center justify-center gap-2 w-full px-3 py-3 rounded-lg cursor-pointer',
                    'border border-dashed border-[#2D3748] bg-[#252B3B]/40 text-[13px] text-[#94A3B8]',
                    'hover:border-blue-500/40 hover:bg-[#252B3B] transition-colors',
                  )}
                >
                  <Paperclip className="h-4 w-4 text-[#64748B]" aria-hidden />
                  Add an image, PDF, or document to process
                  <input
                    type="file"
                    className="sr-only"
                    multiple
                    accept="image/*,.pdf,.txt,.csv,.md,.json,.docx"
                    onChange={(e) => addFiles(e.target.files)}
                    aria-label="Add attachments"
                  />
                </label>
                {files.length > 0 && (
                  <ul className="space-y-1.5">
                    {files.map((f, i) => (
                      <li
                        key={`${f.name}-${i}`}
                        className="flex items-center gap-2 rounded-lg bg-[#252B3B] border border-[#2D3748] px-2.5 py-1.5"
                      >
                        <FileText className="h-3.5 w-3.5 shrink-0 text-[#00D4FF]" aria-hidden />
                        <span className="flex-1 min-w-0 truncate text-[12px] text-[#CBD5E1]">{f.name}</span>
                        <span className="shrink-0 text-[11px] text-[#475569] tabular-nums">
                          {(f.size / 1024).toFixed(0)} KB
                        </span>
                        <button
                          type="button"
                          onClick={() => setFiles((prev) => prev.filter((_, idx) => idx !== i))}
                          aria-label={`Remove ${f.name}`}
                          className="shrink-0 p-1 rounded text-[#64748B] hover:text-rose-300 hover:bg-white/5 transition-colors"
                        >
                          <X className="h-3.5 w-3.5" aria-hidden />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {uploadError && (
                  <p role="alert" className="text-xs text-rose-400">{uploadError}</p>
                )}
              </div>

              {/* Priority + autonomy row */}
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <label htmlFor={`${formId}-priority`} className="block text-sm font-medium text-[#F1F5F9]">
                    Priority
                  </label>
                  <select
                    id={`${formId}-priority`}
                    {...register('priority')}
                    className={cn(
                      'w-full px-3 py-2.5 rounded-lg text-[14px]',
                      'bg-[#252B3B] border border-[#2D3748] text-[#F1F5F9]',
                      'focus:outline-none focus:ring-2 focus:ring-blue-500/60',
                      'transition-colors duration-150',
                    )}
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="critical">Critical</option>
                  </select>
                </div>
                <div className="space-y-1.5">
                  <label htmlFor={`${formId}-autonomy`} className="block text-sm font-medium text-[#F1F5F9]">
                    Autonomy level
                  </label>
                  <select
                    id={`${formId}-autonomy`}
                    {...register('autonomy_level', { valueAsNumber: true })}
                    className={cn(
                      'w-full px-3 py-2.5 rounded-lg text-[14px]',
                      'bg-[#252B3B] border border-[#2D3748] text-[#F1F5F9]',
                      'focus:outline-none focus:ring-2 focus:ring-blue-500/60',
                      'transition-colors duration-150',
                    )}
                  >
                    {[1, 2, 3, 4, 5].map(n => (
                      <option key={n} value={n}>Level {n}</option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Submit (web-guidelines: stays enabled until request starts) */}
              <button
                type="submit"
                aria-label={busy || isSubmitting ? 'Creating mission…' : 'Create mission'}
                disabled={busy || isSubmitting}
                style={{ touchAction: 'manipulation' }}
                className={cn(
                  'flex w-full items-center justify-center gap-2 py-3 rounded-xl text-[15px] font-semibold',
                  'bg-blue-600 hover:bg-blue-500 text-white',
                  'disabled:opacity-50 disabled:cursor-not-allowed',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
                  // emil-design-eng: press state
                  'active:scale-[0.98] transition-[background-color,transform] duration-150',
                )}
              >
                {(busy || isSubmitting) && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
                {/* web-guidelines: loading state ends with … */}
                {files.length > 0 && busy
                  ? 'Uploading & launching…'
                  : busy || isSubmitting
                    ? 'Creating…'
                    : 'Create Mission'}
              </button>
            </form>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
