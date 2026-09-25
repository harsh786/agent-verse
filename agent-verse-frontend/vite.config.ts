import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig(({ command, mode }) => {
  // Single source of truth for the backend URL: the dev proxy target derives
  // from the SAME VITE_API_BASE_URL the API client uses, so components that call
  // through the /api/v1 proxy (Graphify, Obsidian, Command History, Knowledge)
  // always reach the same backend as everything else — no port drift.
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_BASE_URL || "http://localhost:8001";
  return {
  plugins: [
    react(),
    // In test mode: stub all CSS files to prevent sucrase failing on
    // modern CSS features like color-mix() used in @xyflow/react
    ...(mode === "test" ? [{
      name: "stub-css",
      enforce: "pre" as const,
      resolveId(id: string) {
        if (id.endsWith(".css")) return "\0stub-css";
      },
      load(id: string) {
        if (id === "\0stub-css") return "export default {}";
      },
    }] : []),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    // NO hand-rolled `manualChunks`. The previous version grouped node_modules
    // by substring match, which shipped a completely BLANK production app:
    //
    //   * `react-dom` and `scheduler` were routed to "vendor-react" while the
    //     `react` package itself matched no rule and fell through to "vendor",
    //     so react-dom initialised before React existed:
    //       TypeError: Cannot set properties of undefined (setting 'Children')
    //   * behind that, splitting the heavily inter-dependent `d3-*` / `@xyflow`
    //     packages into "vendor-graph" produced a cross-chunk circular import:
    //       ReferenceError: Cannot access 'El' before initialization
    //
    // Both are invisible in dev (unbundled ESM) and to vitest (jsdom, no
    // bundling) — only a real production build shows them. Assigning modules to
    // chunks by name cannot respect the initialisation order that circular
    // dependencies require; Rollup's automatic splitting does. Route-level lazy
    // loading still gives per-page chunks, and the largest chunk is now smaller
    // than it was under the manual scheme.
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: {
      // Proxy /api/v1/* → <VITE_API_BASE_URL>/v1/*
      // GraphifyProgress and other components use the /api/v1/ prefix.
      '/api/v1': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      // Proxy the collaboration WebSockets (org viewer presence, doc sessions)
      // same-origin so the browser connects to :5173 and Vite forwards to the
      // backend — no separate WS host/port to configure, works in prod too.
      '/collab': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    exclude: ["e2e/**", "node_modules/**", "**/dist/**"],
  },
  };
});
