import { defineConfig } from 'vite'
import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'

// Surface the add-on version (from mymeal/config.yaml) to the SPA at build time so
// a bug report can include it. Falls back to 'dev' when the manifest isn't present.
function appVersion() {
  try {
    const yaml = readFileSync(fileURLToPath(new URL('../mymeal/config.yaml', import.meta.url)), 'utf8')
    const m = yaml.match(/^version:\s*["']?([^"'\n]+)["']?/m)
    return m ? m[1].trim() : 'dev'
  } catch {
    return 'dev'
  }
}

export default defineConfig({
  // Relative base so the built app works under a Home Assistant ingress path.
  base: './',
  plugins: [vue()],
  define: { __APP_VERSION__: JSON.stringify(appVersion()) },
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
  test: {
    environment: 'jsdom',
    globals: true,
  },
})
