import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  sendChat,
  fetchThreads,
  fetchThreadMessages,
  deleteThread,
  renameThread,
} from '../api/chat'

export const useChatStore = defineStore('chat', () => {
  const threads = ref([])
  const currentThreadId = ref(null)
  // 当前会话的消息，role 为 'Human' | 'AI'
  const messages = ref([])

  const sending = ref(false)
  const loadingThreads = ref(false)
  const loadingMessages = ref(false)

  async function loadThreads() {
    loadingThreads.value = true
    try {
      threads.value = await fetchThreads()
    } finally {
      loadingThreads.value = false
    }
  }

  async function openThread(threadId) {
    if (threadId === currentThreadId.value) return
    currentThreadId.value = threadId
    loadingMessages.value = true
    messages.value = []
    try {
      messages.value = await fetchThreadMessages(threadId)
    } finally {
      loadingMessages.value = false
    }
  }

  function startNewThread() {
    currentThreadId.value = null
    messages.value = []
  }

  async function send(question) {
    const text = question.trim()
    if (!text || sending.value) return

    messages.value.push({
      role: 'Human',
      content: text,
      sources: [],
      verification: null,
    })

    sending.value = true
    try {
      const res = await sendChat(currentThreadId.value, text)

      messages.value.push({
        role: 'AI',
        content: res.answer ?? '',
        sources: res.sources ?? [],
        verification: res.verification ?? null,
      })

      // 新会话：后端会创建 thread 并回传 thread_id
      const returnedId = res.thread_id
      if (returnedId && returnedId !== currentThreadId.value) {
        currentThreadId.value = returnedId
        await loadThreads()
      }
    } catch (err) {
      messages.value.push({
        role: 'AI',
        content: `请求失败：${err.message}`,
        sources: [],
        verification: null,
        error: true,
      })
    } finally {
      sending.value = false
    }
  }

  async function removeThread(threadId) {
    await deleteThread(threadId)
    if (currentThreadId.value === threadId) {
      startNewThread()
    }
    await loadThreads()
  }

  async function rename(threadId, newTitle) {
    await renameThread(threadId, newTitle)
    await loadThreads()
  }

  return {
    threads,
    currentThreadId,
    messages,
    sending,
    loadingThreads,
    loadingMessages,
    loadThreads,
    openThread,
    startNewThread,
    send,
    removeThread,
    rename,
  }
})
