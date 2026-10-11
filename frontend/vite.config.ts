import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The production bundle is served by FastAPI under a strict CSP
// (no inline scripts or styles, no third-party origins).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
