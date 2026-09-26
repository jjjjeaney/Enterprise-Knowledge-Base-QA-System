<script setup>
import { ref } from 'vue'
import { useChatStore } from '../stores/chat'

const store = useChatStore()
const draft = ref('')

async function handleSend() {
  const text = draft.value.trim()
  if (!text || store.sending) return
  draft.value = ''
  await store.send(text)
}

// Enter 发送，Shift+Enter 换行
function handleKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="chat-input">
    <div class="chat-input__inner">
      <el-input
        v-model="draft"
        type="textarea"
        :rows="2"
        :autosize="{ minRows: 2, maxRows: 6 }"
        resize="none"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行"
        :disabled="store.sending"
        @keydown="handleKeydown"
      />
      <el-button
        type="primary"
        size="large"
        :loading="store.sending"
        :disabled="!draft.trim() || store.sending"
        @click="handleSend"
      >
        发送
      </el-button>
    </div>
    <div class="chat-input__hint">
      回答由大模型基于企业知识库生成，请核对来源后使用。
    </div>
  </div>
</template>
