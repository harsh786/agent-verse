/**
 * useCanvasKeyboardShortcuts — keyboard controls for the workflow canvas.
 *
 * Ctrl+Z  → undo
 * Ctrl+Y / Ctrl+Shift+Z → redo
 * Ctrl+D  → duplicate selected node
 * Delete / Backspace → remove selected nodes/edges
 * Space+drag → pan (handled natively by React Flow)
 */
import { useEffect, useCallback } from 'react';
import type { ReactFlowInstance } from '@xyflow/react';

interface UseShortcutsOptions {
  rfInstance: ReactFlowInstance | null;
  undo: () => void;
  redo: () => void;
  onDeleteSelected: () => void;
  onDuplicateSelected: () => void;
  enabled?: boolean;
}

export function useCanvasKeyboardShortcuts({
  rfInstance,
  undo,
  redo,
  onDeleteSelected,
  onDuplicateSelected,
  enabled = true,
}: UseShortcutsOptions): void {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!enabled) return;
      // Don't fire when focused in a text input, textarea, or monaco editor
      const target = e.target as HTMLElement;
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.closest('.monaco-editor') ||
        target.closest('[contenteditable="true"]')
      ) return;

      const ctrl = e.ctrlKey || e.metaKey;

      if (ctrl && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if (ctrl && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
        e.preventDefault();
        redo();
      } else if (ctrl && e.key === 'd') {
        e.preventDefault();
        onDuplicateSelected();
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA') {
          e.preventDefault();
          onDeleteSelected();
        }
      } else if (ctrl && e.key === 'a') {
        e.preventDefault();
        rfInstance?.getNodes().forEach((_n) => {
          rfInstance.setNodes((nodes) =>
            nodes.map((node) => ({ ...node, selected: true }))
          );
        });
      }
    },
    [enabled, undo, redo, onDeleteSelected, onDuplicateSelected, rfInstance]
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);
}
