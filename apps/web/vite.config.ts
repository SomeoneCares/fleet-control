import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The API (apps/api) runs on :8080 in development: `uvicorn fleetcontrol_api.main:app --port 8080`.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8080" },
  },
  test: {
    include: ["src/**/*.test.ts", "scripts/**/*.test.mjs"],
  },
});
