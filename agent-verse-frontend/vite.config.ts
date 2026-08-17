import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig(({ command, mode }) => ({
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
  server: {
    port: 5173,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    exclude: ["e2e/**", "node_modules/**", "**/dist/**"],
  },
}));
