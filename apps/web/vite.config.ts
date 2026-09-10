import path from "node:path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const apiTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:5174"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true },
    },
  },
})
