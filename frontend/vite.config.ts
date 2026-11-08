/**
 * Vite build + dev-server config for the Avon Nightwatch frontend (task T0.1).
 *
 * - react():        JSX + fast refresh.
 * - tailwindcss():  Tailwind v4 (styles live in src/index.css, no tailwind.config file).
 * - VitePWA():      makes the app installable on phones ("Add to Home Screen"). The /field
 *                   view (T6.11) is the start page when installed. Real PNG icons come in T6.11.
 * - alias "@":      import from "@/components/..." instead of long relative paths.
 * - proxy "/api":   in dev, API calls go to the FastAPI backend on port 8000, so no CORS setup is needed.
 */
import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg"],
      manifest: {
        name: "Avon Nightwatch",
        short_name: "Nightwatch",
        description: "Night-shift console for Avon's produced-water network.",
        theme_color: "#0b0f17",
        background_color: "#0b0f17",
        display: "standalone",
        start_url: "/field",
        icons: [{ src: "icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
      },
      workbox: {
        // Never serve API responses from the offline cache: alerts must always be live.
        navigateFallbackDenylist: [/^\/api/],
        // The app bundle (echarts + maplibre) is ~2.7 MB; the default 2 MiB limit would fail the build.
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
      },
    }),
  ],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
