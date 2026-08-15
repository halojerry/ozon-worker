import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base='/app/'：与 worker FastAPI 静态托管挂载路径一致（生产 SPA 托管在 /app，
// 见 worker/src/main.py 的 _mount_webui_static + webui/README.md）。
export default defineConfig({
  plugins: [react()],
  base: '/app/',
  server: {
    host: true,
    port: 5173,
    // 开发环境把 /api 代理到本地 Worker（部署: deploy/docker-compose.yml，API 8080）
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
