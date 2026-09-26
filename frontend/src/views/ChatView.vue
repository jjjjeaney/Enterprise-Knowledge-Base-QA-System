<script setup>
import { computed } from 'vue'
import ThreadSidebar from '../components/ThreadSidebar.vue'
import MessageList from '../components/MessageList.vue'
import ChatInput from '../components/ChatInput.vue'
import { useChatStore } from '../stores/chat'

const store = useChatStore()

const headerTitle = computed(() => {
  if (store.currentThreadId === null) return '新会话'
  const current = store.threads.find((t) => t.id === store.currentThreadId)
  return current?.title ?? '会话'
})
</script>

<template>
  <div class="chat-layout">
    <ThreadSidebar />
    <main class="chat-main">
      <header class="chat-header">{{ headerTitle }}</header>
      <MessageList />
      <ChatInput />
    </main>
  </div>
</template>
