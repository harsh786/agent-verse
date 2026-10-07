import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** Open modals, topmost last: only the topmost one handles Escape / Tab. */
const openModals: object[] = [];

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface ModalProps {
  labelledBy: string;
  describedBy?: string;
  onClose: () => void;
  children: ReactNode;
  /** Tailwind max-width class of the panel. */
  widthClass?: string;
  /** role="alertdialog" for confirmations. */
  role?: 'dialog' | 'alertdialog';
  /** Element to focus first (else the first focusable element). */
  initialFocusRef?: React.RefObject<HTMLElement | null>;
}

/**
 * Accessible modal for the Model Registry: traps Tab / Shift+Tab inside the
 * panel, closes on Escape and on a backdrop click, focuses the first field on
 * open and gives focus back to the element that opened it on close. Colours
 * come from the theme tokens only (card / border / foreground).
 */
export function Modal({
  labelledBy, describedBy, onClose, children, widthClass = 'max-w-lg', role = 'dialog', initialFocusRef,
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const token = {};
    openModals.push(token);
    const first = initialFocusRef?.current ?? panel?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel)?.focus();

    // Document-level so Escape and Tab still work when focus fell to <body>
    // (e.g. the focused button was replaced). Only the topmost modal reacts.
    const onKeyDown = (e: KeyboardEvent) => {
      if (openModals[openModals.length - 1] !== token || !panelRef.current) return;
      const root = panelRef.current;
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) {
        e.preventDefault();
        root.focus();
        return;
      }
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inside = !!active && root.contains(active);
      if (!inside) {
        e.preventDefault();
        (e.shiftKey ? lastEl : firstEl).focus();
      } else if (e.shiftKey && (active === firstEl || active === root)) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && active === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      const i = openModals.indexOf(token);
      if (i !== -1) openModals.splice(i, 1);
      if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus();
    };
    // Set up once on open only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Portalled to <body>: the page shell animates with a transform, which would
  // otherwise turn `position: fixed` into "fixed to the shell".
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/50"
        aria-hidden="true"
        data-testid="modal-backdrop"
        onClick={() => onCloseRef.current()}
      />
      <div
        ref={panelRef}
        role={role}
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        tabIndex={-1}
        className={`relative flex max-h-[90vh] w-full ${widthClass} flex-col rounded-2xl border border-border bg-card text-card-foreground shadow-xl outline-none`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
