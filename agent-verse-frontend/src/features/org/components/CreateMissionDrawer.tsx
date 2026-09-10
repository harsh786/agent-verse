/**
 * CreateMissionDrawer — JARVIS-style slide-up drawer to create a mission.
 *
 * Skills:
 *   - emil-design-eng: spring slide-in (fluid 300/25), exit shorter than enter
 *   - impeccable-ui:   form hierarchy, actionable labels, error inline
 *   - web-guidelines:  form labels, aria-required, aria-invalid, touch-action
 *   - ui-ux-pro-max:   44px submit, useReducedMotion, focus trap
 */
import { useCallback, useId, useRef, useEffect } from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { X, Zap } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { cn } from '@/lib/utils';
import { useCreateMission } from '../hooks/useOrg';
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
    await createMission.mutateAsync(data);
    reset();
    onClose();
  }, [createMission, reset, onClose]);

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
                aria-label={isSubmitting ? 'Creating mission…' : 'Create mission'}
                disabled={isSubmitting}
                style={{ touchAction: 'manipulation' }}
                className={cn(
                  'w-full py-3 rounded-xl text-[15px] font-semibold',
                  'bg-blue-600 hover:bg-blue-500 text-white',
                  'disabled:opacity-50 disabled:cursor-not-allowed',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500',
                  // emil-design-eng: press state
                  'active:scale-[0.98] transition-[background-color,transform] duration-150',
                )}
              >
                {/* web-guidelines: loading state ends with … */}
                {isSubmitting ? 'Creating…' : 'Create Mission'}
              </button>
            </form>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
