import axios from 'axios'

// 后端服务地址；如需改成局域网地址，改这里即可
export const API_BASE_URL = 'http://127.0.0.1:8000'

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
