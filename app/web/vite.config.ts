import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// `npm run dev` forwards API and WebSocket calls to the Python server.
// Override with: set SWING_SERVER=http://127.0.0.1:9000 && npm run dev
const SERVER = process.env.SWING_SERVER ?? "http://127.0.0.1:8000"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 900,
  },
  server: {
    proxy: {
      "/api": { target: SERVER, changeOrigin: true },
      "/ws": { target: SERVER, ws: true },
    },
  },
})
