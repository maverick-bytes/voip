import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig(({ mode: _mode }) => ({
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

  plugins: [
    react(),
    // Re-inject the theme script into the built index.html.
    // Vite strips inline <script> blocks from index.html during build because
    // it can't fingerprint them for CSP. We use transformIndexHtml to put it
    // back so the dark class is applied synchronously before first paint,
    // eliminating the white flash on every page refresh.
    {
      name: "inject-theme-script",
      transformIndexHtml(html) {
        const themeScript = `<script>(function(){var s=localStorage.getItem("voip-theme");var d=s?s==="dark":window.matchMedia("(prefers-color-scheme: dark)").matches;if(d)document.documentElement.classList.add("dark");})()</script>`;
        return html.replace("</head>", `  ${themeScript}\n  </head>`);
      },
    },
  ],

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
