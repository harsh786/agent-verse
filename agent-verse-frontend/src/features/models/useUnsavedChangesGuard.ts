import { useEffect, useRef } from 'react';

export const UNSAVED_ORDER_MESSAGE =
  'You have unsaved preference-order changes. Leave this page and discard them?';

/**
 * Warn before leaving the page with unsaved changes: the browser's own prompt
 * on reload / tab close (beforeunload), and a confirm on in-app link clicks
 * (the app uses a BrowserRouter, which has no navigation blocker API).
 */
export function useUnsavedChangesGuard(dirty: boolean, message = UNSAVED_ORDER_MESSAGE) {
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // Legacy browsers need returnValue set to show the prompt.
      e.returnValue = message;
      return message;
    };
    const onClick = (e: MouseEvent) => {
      if (!dirtyRef.current || e.defaultPrevented || e.button !== 0) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return; // new tab / window
      const anchor = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) return;
      const href = anchor.getAttribute('href') ?? '';
      if (!href || href.startsWith('#')) return;
      if (!window.confirm(message)) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    // Capture phase: runs before the router's own link handler.
    document.addEventListener('click', onClick, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      document.removeEventListener('click', onClick, true);
    };
  }, [dirty, message]);
}
