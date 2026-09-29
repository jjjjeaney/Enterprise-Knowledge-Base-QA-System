import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// ⚠️ 本机的 `npm run dev` 已经连不到后端，现在只够用来预览界面。
//
// 原因：前后端收成了单一源——反代写在 frontend/nginx.conf 里，浏览器只跟 5173 说话，
// 而 app 容器不再发布 8000 端口，所以 dev server 没有可代理的目标。
// 前端开发请走容器：docker compose up -d frontend
//
// 以后若要恢复本机开发：把 docker-compose.yml 里 app 的 "8000:8000" 加回来，
// 再在下面的 server 里加代理（注意必须是 server.proxy——写在最外层 Vite 会直接忽略，
// 这个坑踩过）：
//   proxy: {
//     '/api': {
//       target: 'http://127.0.0.1:8000',
//       changeOrigin: true,
//       rewrite: (p) => p.replace(/^\/api/, ''),
//     },
//   },
export default defineConfig({
  plugins: [vue()],
  server: {
    host: 'localhost',
    port: 5173,
    strictPort: true,
  },
})
