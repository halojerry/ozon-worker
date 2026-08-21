import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import path from 'node:path'

// 生产：worker FastAPI 同进程伺服 /app（WEBUI_DIST bind mount），base 必须 /app/
// 开发：vite dev server（5173），/api/v1 代理到本地 worker（8080）
export default defineConfig({
  base: '/app/',
  plugins: [tanstackRouter(), react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api/v1': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
