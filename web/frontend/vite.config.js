import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    // Pure-lib tests run under node; DOM-dependent test files opt into
    // jsdom via a `@vitest-environment jsdom` docblock at the top.
    environment: 'node',
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // Keep previous builds' hashed assets. Google renders a page hours or
    // days after crawling its HTML; with the default (true) every build
    // deleted the chunks that HTML referenced and the render came back
    // blank (Googlebot hit a 404 on a stale index-*.css on 2026-10-05).
    // scripts/prune-assets.mjs removes assets older than 30 days after
    // each build.
    emptyOutDir: false,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
})
