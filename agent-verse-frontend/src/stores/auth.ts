import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface AuthState {
  apiKey: string;
  tenantId: string;
  plan: string;
  isAuthenticated: boolean;
  setCredentials: (apiKey: string, tenantId: string, plan: string) => void;
  login: (creds: { apiKey: string; tenantId: string }) => void;
  logout: () => void;
}

// Use sessionStorage (cleared on tab close) to reduce XSS exfiltration risk.
// Falls back to localStorage for backward compat on reads only — never writes
// API keys back to localStorage.
const secureStorage = createJSONStorage(() => ({
  getItem: (name: string): string | null => {
    return sessionStorage.getItem(name) ?? localStorage.getItem(name);
  },
  setItem: (name: string, value: string): void => {
    sessionStorage.setItem(name, value);
    // Do NOT write to localStorage so API keys are not persisted to disk.
  },
  removeItem: (name: string): void => {
    sessionStorage.removeItem(name);
    localStorage.removeItem(name);
  },
}));

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      apiKey: "",
      tenantId: "",
      plan: "",
      isAuthenticated: false,
      setCredentials: (apiKey, tenantId, plan) => {
        sessionStorage.setItem("av_api_key", apiKey);
        localStorage.removeItem("av_api_key"); // migration: remove from localStorage
        set({ apiKey, tenantId, plan, isAuthenticated: true });
      },
      login: (creds) => {
        sessionStorage.setItem("av_api_key", creds.apiKey);
        localStorage.removeItem("av_api_key"); // migration: remove from localStorage
        set({ apiKey: creds.apiKey, tenantId: creds.tenantId, plan: "", isAuthenticated: true });
      },
      logout: () => {
        sessionStorage.removeItem("av_api_key");
        localStorage.removeItem("av_api_key");
        sessionStorage.removeItem("av-auth");
        localStorage.removeItem("av-auth");
        set({ apiKey: "", tenantId: "", plan: "", isAuthenticated: false });
      },
    }),
    { name: "av-auth", storage: secureStorage }
  )
);
