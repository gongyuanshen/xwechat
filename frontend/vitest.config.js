import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  plugins: [vue({ template: { transformAssetUrls: false } })],
  resolve: {
    alias: [
      { find: '~', replacement: fileURLToPath(new URL('.', import.meta.url)) },
      { find: '@', replacement: fileURLToPath(new URL('.', import.meta.url)) },
    ]
  },
  server: {
    fs: { allow: [fileURLToPath(new URL('..', import.meta.url))] }
  },
  test: {
    environment: 'happy-dom',
    setupFiles: ['./tests/setup.js'],
    include: ['tests/**/*.test.js']
  }
})
