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
    // Split heavy, independently-cacheable vendor libraries out of the main
    // entry chunk so first paint downloads far less and returning visitors reuse
    // cached vendor bundles. (The app itself is already route-level lazy-loaded.)
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("framer-motion") || id.includes("motion-dom") || id.includes("motion-utils"))
            return "vendor-motion";
          if (id.includes("@xyflow") || id.includes("reactflow") || id.includes("d3-"))
            return "vendor-graph";
          if (id.includes("codemirror") || id.includes("@uiw/react-codemirror"))
            return "vendor-editor";
          if (id.includes("/three/") || id.includes("@react-three")) return "vendor-three";
          if (id.includes("yjs") || id.includes("y-websocket") || id.includes("y-protocols"))
            return "vendor-collab";
          if (id.includes("i18next") || id.includes("react-i18next")) return "vendor-i18n";
          if (id.includes("@tanstack")) return "vendor-query";
          if (id.includes("react-dom") || id.includes("/scheduler/")) return "vendor-react";
          return "vendor";
        },
      },
    },
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
