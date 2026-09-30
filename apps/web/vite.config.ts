import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The API (apps/api) runs on :8080 in development: `uvicorn fleetcontrol_api.main:app --port 8080`.
// FLEETCONTROL_WEB_PORT and FLEETCONTROL_API_PORT move both, so two checkouts can run side by side
// (scripts/start-dev.ps1 does this).
const webPort = Number(process.env.FLEETCONTROL_WEB_PORT ?? 5173);
const apiPort = Number(process.env.FLEETCONTROL_API_PORT ?? 8080);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: webPort,
    strictPort: true,
    proxy: { "/api": `http://127.0.0.1:${apiPort}` },
  },
  test: {
    include: ["src/**/*.test.ts", "scripts/**/*.test.mjs"],
  },
});
