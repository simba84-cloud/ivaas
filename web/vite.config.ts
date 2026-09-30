import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The end-to-end run starts its own API on another port; see playwright.config.ts.
const api = process.env.IVAAS_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": api,
      "/ws": { target: api.replace(/^http/, "ws"), ws: true },
    },
  },
});
