import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { TanStackRouterVite } from '@tanstack/router-vite-plugin'
import path from 'path'
import type { IncomingMessage } from 'http'

// Skip proxying when the browser is doing an HTML page load (reload / navigate).
// API calls use Accept: application/json and will still be proxied normally.
const skipHtml = (req: IncomingMessage) => {
  if (req.headers.accept?.includes('text/html')) return req.url ?? '/'
  return undefined
}

const flask = { target: 'http://127.0.0.1:5000', changeOrigin: true }

export default defineConfig({
  plugins: [
    react(),
    TanStackRouterVite(),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    host: true,   // bind to 0.0.0.0 so LAN machines can reach the dev server
    port: 5173,
    proxy: {
      '/api':         flask,
      '/jobs':        { ...flask, bypass: skipHtml },
      '/mods':        { ...flask, bypass: skipHtml },
      '/logs':        { ...flask, bypass: skipHtml },
      '/servers':     { ...flask, bypass: skipHtml },
      '/config':      { ...flask, bypass: skipHtml },
      '/terminology': { ...flask, bypass: skipHtml },
      '/backups':     { ...flask, bypass: skipHtml },
      '/tools':       { ...flask, bypass: skipHtml },
      '/setup.sh':    flask,
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
