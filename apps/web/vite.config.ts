import path from "node:path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8300"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 8000,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
  preview: {
    port: 8000,
  },
})
