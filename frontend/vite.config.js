import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  // Relative base so the built app works under a Home Assistant ingress path.
  base: './',
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:7850',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
