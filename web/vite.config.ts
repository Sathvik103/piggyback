import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API (FastAPI) serves the built app at /app/. In dev, Vite proxies API calls to it.
const API = process.env.PIGGYBACK_API ?? 'http://localhost:8000'
const apiPaths = ['/scenarios', '/benchmark', '/health']

export default defineConfig({
  base: '/app/',
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  build: { outDir: '../api/static', emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: { proxy: Object.fromEntries(apiPaths.map((p) => [p, API])) },
})
