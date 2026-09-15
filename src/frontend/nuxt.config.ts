// 桌面场景无 SSR 需求：Nuxt 跑 SPA 模式，产物交 Tauri 打包。
// 参考 https://v2.tauri.app/start/frontend/nuxt/
export default defineNuxtConfig({
  compatibilityDate: '2025-05-15',
  ssr: false,
  telemetry: false,
  devtools: { enabled: false },
  modules: ['@pinia/nuxt', './modules/dev-backend'],
  components: [{ path: '~/components', pathPrefix: false }],
  css: ['~/assets/main.css'],
  router: {
    options: {
      hashMode: true,
    },
  },
  devServer: {
    host: '127.0.0.1',
    port: 3000,
  },
  vite: {
    // Tauri CLI 输出与一致端口（devUrl 依赖固定端口）
    clearScreen: false,
    envPrefix: ['VITE_', 'TAURI_'],
    server: { strictPort: true },
  },
  // 避免 Nuxt 监听 Rust 工程
  ignore: ['**/src-tauri/**'],
})
