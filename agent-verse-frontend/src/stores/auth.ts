import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AuthState {
  apiKey: string;
  tenantId: string;
  plan: string;
  isAuthenticated: boolean;
  setCredentials: (apiKey: string, tenantId: string, plan: string) => void;
  login: (creds: { apiKey: string; tenantId: string }) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      apiKey: "",
      tenantId: "",
      plan: "",
      isAuthenticated: false,
      setCredentials: (apiKey, tenantId, plan) => {
        localStorage.setItem("av_api_key", apiKey);
        set({ apiKey, tenantId, plan, isAuthenticated: true });
      },
      login: (creds) => {
        localStorage.setItem("av_api_key", creds.apiKey);
        set({ apiKey: creds.apiKey, tenantId: creds.tenantId, plan: "", isAuthenticated: true });
      },
      logout: () => {
        localStorage.removeItem("av_api_key");
        set({ apiKey: "", tenantId: "", plan: "", isAuthenticated: false });
      },
    }),
    { name: "av-auth" }
  )
);
