import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 后端 CORS 只放行了 http://localhost:5173，这里保持同样的 host/port。
// strictPort 必须为 true：端口被占时默认会静默改用 5174，
// 那样页面照常打开、但所有接口都会被 CORS 拦掉，排查起来非常费劲。
export default defineConfig({
  plugins: [vue()],
  server: {
    host: 'localhost',
    port: 5173,
    strictPort: true,
  },
})
