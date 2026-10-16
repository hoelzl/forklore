import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { inspectAttr } from 'kimi-plugin-inspect-react'

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [inspectAttr(), react()],
  server: {
    port: 3000,
    // dev: proxy API + SSE to the forklore server (python -m server)
    proxy: Object.fromEntries(
      ['/nodes', '/state', '/branch', '/events', '/strategies', '/generate', '/undo']
        .map((p) => [p, { target: 'http://localhost:8000', changeOrigin: true }]),
    ),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
