import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { TanStackRouterVite } from '@tanstack/router-vite-plugin'
import path from 'path'

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
      '/api':         { target: 'http://127.0.0.1:5000', changeOrigin: true },
      '/jobs':        { target: 'http://127.0.0.1:5000', changeOrigin: true },
      '/mods':        { target: 'http://127.0.0.1:5000', changeOrigin: true },
      '/logs':        { target: 'http://127.0.0.1:5000', changeOrigin: true },
      '/servers':     { target: 'http://127.0.0.1:5000', changeOrigin: true },
      '/config':      { target: 'http://127.0.0.1:5000', changeOrigin: true },
      '/terminology': { target: 'http://127.0.0.1:5000', changeOrigin: true },
      '/backups':     { target: 'http://127.0.0.1:5000', changeOrigin: true },
      '/tools':       { target: 'http://127.0.0.1:5000', changeOrigin: true },
      '/setup.sh':    { target: 'http://127.0.0.1:5000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
