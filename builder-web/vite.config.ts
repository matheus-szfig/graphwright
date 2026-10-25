import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// Served under /builder/ — behind nginx (http://localhost:8080/builder/) or directly
// (http://localhost:5174/builder/). The API is always addressed as <base>/api; in
// standalone dev the proxy below forwards it to the builder-api on :8765, behind
// nginx that path is routed before it ever reaches Vite.
export default defineConfig({
  base: "/builder/",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5174,
    host: true,
    proxy: {
      "/builder/api": {
        target: process.env.BUILDER_API_URL ?? "http://localhost:8765",
        changeOrigin: true,
        ws: true,
        rewrite: (p) => p.replace(/^\/builder\/api/, ""),
      },
    },
  },
})
