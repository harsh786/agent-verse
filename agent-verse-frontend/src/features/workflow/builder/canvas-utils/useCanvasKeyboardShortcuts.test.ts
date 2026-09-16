import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { useCanvasKeyboardShortcuts } from './useCanvasKeyboardShortcuts';

type Cbs = {
  undo: ReturnType<typeof vi.fn>;
  redo: ReturnType<typeof vi.fn>;
  onDeleteSelected: ReturnType<typeof vi.fn>;
  onDuplicateSelected: ReturnType<typeof vi.fn>;
};

function makeCbs(): Cbs {
  return {
    undo: vi.fn(),
    redo: vi.fn(),
    onDeleteSelected: vi.fn(),
    onDuplicateSelected: vi.fn(),
  };
}

function mount(cbs: Cbs, opts: Partial<{ enabled: boolean; rfInstance: unknown }> = {}) {
  return renderHook(() =>
    useCanvasKeyboardShortcuts({
      rfInstance: (opts.rfInstance ?? null) as never,
      undo: cbs.undo,
      redo: cbs.redo,
      onDeleteSelected: cbs.onDeleteSelected,
      onDuplicateSelected: cbs.onDuplicateSelected,
      enabled: opts.enabled ?? true,
    }),
  );
}

/** Dispatch a keydown on `target` (defaults to document.body). */
function press(key: string, init: Partial<KeyboardEventInit> & { target?: HTMLElement } = {}) {
  const { target = document.body, ...rest } = init;
  const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...rest });
  target.dispatchEvent(ev);
  return ev;
}

afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('useCanvasKeyboardShortcuts', () => {
  test('Ctrl+Z triggers undo and prevents default', () => {
    const cbs = makeCbs();
    mount(cbs);
    const ev = press('z', { ctrlKey: true });
    expect(cbs.undo).toHaveBeenCalledTimes(1);
    expect(cbs.redo).not.toHaveBeenCalled();
    expect(ev.defaultPrevented).toBe(true);
  });

  test('Ctrl+Y and Ctrl+Shift+Z both trigger redo', () => {
    const cbs = makeCbs();
    mount(cbs);
    press('y', { ctrlKey: true });
    press('z', { ctrlKey: true, shiftKey: true });
    expect(cbs.redo).toHaveBeenCalledTimes(2);
    expect(cbs.undo).not.toHaveBeenCalled();
  });

  test('Ctrl+D duplicates the selected node', () => {
    const cbs = makeCbs();
    mount(cbs);
    press('d', { ctrlKey: true });
    expect(cbs.onDuplicateSelected).toHaveBeenCalledTimes(1);
  });

  test('Delete and Backspace both delete the selection', () => {
    const cbs = makeCbs();
    mount(cbs);
    press('Delete');
    press('Backspace');
    expect(cbs.onDeleteSelected).toHaveBeenCalledTimes(2);
  });

  test('meta key (cmd) also counts as ctrl for undo', () => {
    const cbs = makeCbs();
    mount(cbs);
    press('z', { metaKey: true });
    expect(cbs.undo).toHaveBeenCalledTimes(1);
  });

  test('does nothing when typing inside an input element', () => {
    const cbs = makeCbs();
    mount(cbs);
    const input = document.createElement('input');
    document.body.appendChild(input);
    press('z', { ctrlKey: true, target: input });
    press('Delete', { target: input });
    expect(cbs.undo).not.toHaveBeenCalled();
    expect(cbs.onDeleteSelected).not.toHaveBeenCalled();
  });

  test('does nothing when disabled', () => {
    const cbs = makeCbs();
    mount(cbs, { enabled: false });
    press('z', { ctrlKey: true });
    press('Delete');
    expect(cbs.undo).not.toHaveBeenCalled();
    expect(cbs.onDeleteSelected).not.toHaveBeenCalled();
  });

  test('Ctrl+A selects all nodes via the React Flow instance', () => {
    const cbs = makeCbs();
    const nodes = [{ id: 'n1', selected: false }];
    const setNodes = vi.fn((updater: (n: typeof nodes) => typeof nodes) => updater(nodes));
    const rfInstance = { getNodes: () => nodes, setNodes };
    mount(cbs, { rfInstance });
    press('a', { ctrlKey: true });
    expect(setNodes).toHaveBeenCalled();
    // The updater marks every node selected.
    expect(setNodes.mock.results[0].value).toEqual([{ id: 'n1', selected: true }]);
  });

  test('removes its keydown listener on unmount', () => {
    const cbs = makeCbs();
    const { unmount } = mount(cbs);
    unmount();
    press('z', { ctrlKey: true });
    expect(cbs.undo).not.toHaveBeenCalled();
  });
});
