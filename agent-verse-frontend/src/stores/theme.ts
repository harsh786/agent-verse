import { create } from 'zustand';
import { persist } from 'zustand/middleware';

type Theme = 'light' | 'dark' | 'system';
type Density = 'compact' | 'default' | 'comfortable';

interface ThemeState {
  theme: Theme;
  density: Density;
  setTheme: (theme: Theme) => void;
  setDensity: (density: Density) => void;
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const isDark = theme === 'dark' || (theme === 'system' && prefersDark);
  root.classList.toggle('dark', isDark);
}

function applyDensity(density: Density) {
  const root = document.documentElement;
  root.setAttribute('data-density', density);
  const densityMap: Record<Density, string> = {
    compact: '0.25rem',
    default: '0.5rem',
    comfortable: '0.75rem',
  };
  root.style.setProperty('--density-space', densityMap[density]);
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      theme: 'dark',     // JARVIS: dark is the product identity — not optional
      density: 'default',
      setTheme: (theme) => {
        applyTheme(theme);
        set({ theme });
      },
      setDensity: (density) => {
        applyDensity(density);
        set({ density });
      },
    }),
    { name: 'av-theme' }
  )
);

// Apply on load
if (typeof window !== 'undefined') {
  const stored = useThemeStore.getState();
  applyTheme(stored.theme);
  applyDensity(stored.density);

  // Watch system preference changes
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (useThemeStore.getState().theme === 'system') {
      applyTheme('system');
    }
  });
}
