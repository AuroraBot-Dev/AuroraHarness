import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: {
      '~': fileURLToPath(new URL('./app', import.meta.url)),
    },
  },
  test: {
    environment: 'happy-dom',
    exclude: ['e2e/**', 'node_modules/**'],
    restoreMocks: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['app/stores/**/*.ts', 'app/utils/**/*.ts'],
      thresholds: { statements: 40, branches: 35, functions: 40, lines: 45 },
    },
  },
})
