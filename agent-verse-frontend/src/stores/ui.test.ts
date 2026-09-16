import { beforeEach, describe, expect, it, vi } from 'vitest';

beforeEach(() => {
  document.documentElement.className = '';
  localStorage.clear();
  vi.resetModules();
});

describe('useUiStore', () => {
  it('defaults to sidebar open, command palette closed, and light theme', async () => {
    const { useUiStore } = await import('./ui');
    const s = useUiStore.getState();
    expect(s.sidebarOpen).toBe(true);
    expect(s.commandPaletteOpen).toBe(false);
    expect(s.theme).toBe('light');
  });

  it('toggleSidebar flips sidebarOpen back and forth', async () => {
    const { useUiStore } = await import('./ui');
    useUiStore.getState().toggleSidebar();
    expect(useUiStore.getState().sidebarOpen).toBe(false);
    useUiStore.getState().toggleSidebar();
    expect(useUiStore.getState().sidebarOpen).toBe(true);
  });

  it('openCommandPalette / closeCommandPalette set the flag', async () => {
    const { useUiStore } = await import('./ui');
    useUiStore.getState().openCommandPalette();
    expect(useUiStore.getState().commandPaletteOpen).toBe(true);
    useUiStore.getState().closeCommandPalette();
    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
  });

  it('setTheme("dark") applies the dark class and updates state', async () => {
    const { useUiStore } = await import('./ui');
    useUiStore.getState().setTheme('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(useUiStore.getState().theme).toBe('dark');
  });

  it('setTheme("light") removes the dark class', async () => {
    const { useUiStore } = await import('./ui');
    useUiStore.getState().setTheme('dark');
    useUiStore.getState().setTheme('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('toggleTheme flips from light to dark', async () => {
    const { useUiStore } = await import('./ui');
    useUiStore.getState().toggleTheme();
    expect(useUiStore.getState().theme).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('toggleTheme flips from dark back to light', async () => {
    const { useUiStore } = await import('./ui');
    useUiStore.getState().toggleTheme();
    useUiStore.getState().toggleTheme();
    expect(useUiStore.getState().theme).toBe('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('persists only theme and sidebarOpen (partialize), not commandPaletteOpen', async () => {
    const { useUiStore } = await import('./ui');
    useUiStore.getState().openCommandPalette();
    useUiStore.getState().setTheme('dark');
    const raw = localStorage.getItem('av-ui');
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw as string);
    expect(parsed.state.theme).toBe('dark');
    expect(parsed.state.sidebarOpen).toBe(true);
    expect(parsed.state.commandPaletteOpen).toBeUndefined();
  });

  it('applies a persisted theme to the DOM on rehydration (onRehydrateStorage)', async () => {
    localStorage.setItem(
      'av-ui',
      JSON.stringify({ state: { theme: 'dark', sidebarOpen: false }, version: 0 }),
    );
    const { useUiStore } = await import('./ui');
    await useUiStore.persist.rehydrate();
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(useUiStore.getState().sidebarOpen).toBe(false);
  });
});
