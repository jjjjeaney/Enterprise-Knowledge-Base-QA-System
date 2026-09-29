import axios from 'axios'

// 相对路径，走的是前端 nginx 的反代（见 frontend/nginx.conf 的 location /api/）。
// 要换后端地址就改那里的 proxy_pass —— 不是改这里，这里不再有绝对地址。
export const API_BASE_URL = '/api'

const client = axios.create({
  baseURL: API_BASE_URL,
  // /chat 要走完整的 RAG + LLM 流程，可能非常慢，这里给足超时时间
  timeout: 180000,
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    // 后端出错时走 HTTPException，detail 里是原始错误信息
    const detail = error.response?.data?.detail
    const message =
      (typeof detail === 'string' && detail) ||
      error.message ||
      '请求失败'
    return Promise.reject(new Error(message))
  },
)

export default client
