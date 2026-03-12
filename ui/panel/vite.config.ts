import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  // In production the app is served under /voip by nginx.
  // This makes all asset paths relative to /voip so they load correctly.
  base: "/voip/",

  server: {
    host: "::",
    port: 8080,
    hmr: { overlay: false },
    proxy: {
      // During local dev, forward API calls to a locally running server.py
      // Start it with: python3 ui/server.py  (from the repo root)
      "/voip/api": {
        target: "http://127.0.0.1:8099",
        changeOrigin: true,
      },
    },
  },

  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
