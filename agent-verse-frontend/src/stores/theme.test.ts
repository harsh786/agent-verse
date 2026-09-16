import { beforeEach, describe, expect, it, vi } from 'vitest';

type Listener = () => void;

function mockMatchMedia(matches: boolean) {
  const listeners: Listener[] = [];
  const mql = {
    matches,
    media: '(prefers-color-scheme: dark)',
    addEventListener: (_: string, cb: Listener) => {
      listeners.push(cb);
    },
    removeEventListener: () => undefined,
  };
  window.matchMedia = vi.fn().mockReturnValue(mql) as unknown as typeof window.matchMedia;
  return { listeners };
}

beforeEach(() => {
  document.documentElement.className = '';
  document.documentElement.removeAttribute('data-density');
  document.documentElement.style.removeProperty('--density-space');
  localStorage.clear();
  vi.resetModules();
});

describe('useThemeStore', () => {
  it('defaults to a dark theme and default density', async () => {
    mockMatchMedia(false);
    const { useThemeStore } = await import('./theme');
    const s = useThemeStore.getState();
    expect(s.theme).toBe('dark');
    expect(s.density).toBe('default');
  });

  it('applies the dark class to <html> on load for the default dark theme', async () => {
    mockMatchMedia(false);
    await import('./theme');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('setTheme("light") removes the dark class and updates state', async () => {
    mockMatchMedia(false);
    const { useThemeStore } = await import('./theme');
    useThemeStore.getState().setTheme('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(useThemeStore.getState().theme).toBe('light');
  });

  it('setTheme("dark") adds the dark class', async () => {
    mockMatchMedia(false);
    const { useThemeStore } = await import('./theme');
    useThemeStore.getState().setTheme('light');
    useThemeStore.getState().setTheme('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('setTheme("system") follows an OS dark preference', async () => {
    mockMatchMedia(true);
    const { useThemeStore } = await import('./theme');
    useThemeStore.getState().setTheme('system');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(useThemeStore.getState().theme).toBe('system');
  });

  it('setTheme("system") follows an OS light preference', async () => {
    mockMatchMedia(false);
    const { useThemeStore } = await import('./theme');
    useThemeStore.getState().setTheme('system');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('setDensity updates the data-density attribute and the CSS variable for each density', async () => {
    mockMatchMedia(false);
    const { useThemeStore } = await import('./theme');

    useThemeStore.getState().setDensity('compact');
    expect(document.documentElement.getAttribute('data-density')).toBe('compact');
    expect(document.documentElement.style.getPropertyValue('--density-space')).toBe('0.25rem');

    useThemeStore.getState().setDensity('default');
    expect(document.documentElement.getAttribute('data-density')).toBe('default');
    expect(document.documentElement.style.getPropertyValue('--density-space')).toBe('0.5rem');

    useThemeStore.getState().setDensity('comfortable');
    expect(document.documentElement.getAttribute('data-density')).toBe('comfortable');
    expect(document.documentElement.style.getPropertyValue('--density-space')).toBe('0.75rem');
  });

  it('reacts to a system color-scheme change while theme==="system"', async () => {
    const { listeners } = mockMatchMedia(false);
    const { useThemeStore } = await import('./theme');
    useThemeStore.getState().setTheme('system');
    expect(document.documentElement.classList.contains('dark')).toBe(false);

    // Flip the OS preference to dark, then fire the 'change' listener that was
    // registered against window.matchMedia at module load time.
    (window.matchMedia as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      matches: true,
      media: '(prefers-color-scheme: dark)',
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    });
    listeners.forEach((cb) => cb());
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('ignores a system color-scheme change when theme is not "system"', async () => {
    const { listeners } = mockMatchMedia(false);
    const { useThemeStore } = await import('./theme');
    useThemeStore.getState().setTheme('light');

    (window.matchMedia as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
      matches: true,
      media: '(prefers-color-scheme: dark)',
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    });
    listeners.forEach((cb) => cb());
    // Still light — the change handler no-ops when theme !== 'system'.
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('persists theme and density under the av-theme key', async () => {
    mockMatchMedia(false);
    const { useThemeStore } = await import('./theme');
    useThemeStore.getState().setTheme('light');
    useThemeStore.getState().setDensity('compact');
    const raw = localStorage.getItem('av-theme');
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw as string);
    expect(parsed.state.theme).toBe('light');
    expect(parsed.state.density).toBe('compact');
  });
});
