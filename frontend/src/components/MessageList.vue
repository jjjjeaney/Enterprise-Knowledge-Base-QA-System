<script setup>
import { nextTick, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'
import MessageAvatar from './MessageAvatar.vue'
import { useChatStore } from '../stores/chat'

const store = useChatStore()
const listRef = ref(null)

function scrollToBottom() {
  const el = listRef.value
  if (el) el.scrollTop = el.scrollHeight
}

// 新消息或打字状态变化时滚到底部
watch(
  () => [store.messages.length, store.sending],
  () => nextTick(scrollToBottom),
)
</script>

<template>
  <div ref="listRef" v-loading="store.loadingMessages" class="message-list">
    <div class="message-list__inner">
      <div v-if="store.messages.length === 0 && !store.sending" class="empty-state">
        <p class="empty-state__title">输入你的问题，开始基于企业知识库的问答</p>
        <p class="empty-state__hint">回答会附带来源文件，便于核对</p>
      </div>

      <MessageBubble
        v-for="(msg, index) in store.messages"
        :key="index"
        :message="msg"
      />

      <div v-if="store.sending" class="message-row is-ai">
        <MessageAvatar />
        <div class="bubble bubble--typing">
          <span class="typing-dot"></span>
          正在生成回答…
        </div>
      </div>
    </div>
  </div>
</template>
