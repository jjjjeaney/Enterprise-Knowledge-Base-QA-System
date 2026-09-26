import { createRouter, createWebHashHistory } from 'vue-router'
import ChatView from '../views/ChatView.vue'
import DocumentsView from '../views/DocumentsView.vue'

const routes = [
  { path: '/', name: 'chat', component: ChatView },
  { path: '/documents', name: 'documents', component: DocumentsView },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export default createRouter({
  // 用 hash 模式，避免生产环境部署静态文件时还要配 history 回退
  history: createWebHashHistory(),
  routes,
})
