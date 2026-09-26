<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import MessageAvatar from './MessageAvatar.vue'

const props = defineProps({
  message: { type: Object, required: true },
})

const isUser = computed(() => props.message.role === 'Human')
const isError = computed(() => Boolean(props.message.error))

// 后端的 sources 是上传文件的绝对路径，这里只展示文件名
function basename(path) {
  if (!path) return ''
  return path.split(/[\\/]/).pop()
}

const sourceNames = computed(() =>
  (props.message.sources || []).map((s) => ({ full: s, name: basename(s) })),
)

const renderedContent = computed(() => {
  if (isUser.value || isError.value) return ''
  const html = marked.parse(props.message.content || '', { breaks: true })
  return DOMPurify.sanitize(html)
})

// verification 可能是 true / false / null（null 表示该分支没有校验）
const verificationTag = computed(() => {
  const v = props.message.verification
  if (v === true) return { type: 'success', text: '已核实' }
  if (v === false) return { type: 'warning', text: '未核实' }
  return null
})
</script>

<template>
  <div class="message-row" :class="isUser ? 'is-user' : 'is-ai'">
    <MessageAvatar :user="isUser" />
    <div class="bubble" :class="{ 'is-error': isError }">
      <template v-if="isUser">
        {{ message.content }}
      </template>

      <div
        v-else-if="!isError"
        class="bubble__markdown"
        v-html="renderedContent"
      ></div>

      <template v-else>
        {{ message.content }}
      </template>

      <!-- AI 消息的来源与核实信息 -->
      <div
        v-if="!isUser && (sourceNames.length > 0 || verificationTag)"
        class="bubble__meta"
      >
        <div v-if="sourceNames.length > 0">
          <div class="bubble__meta-label">来源</div>
          <ol class="citation-list">
            <li v-for="(src, index) in sourceNames" :key="`${index}-${src.full}`">
              <span class="citation-list__index">{{ index + 1 }}</span>
              <span class="citation-list__name" :title="src.full">
                {{ src.name }}
              </span>
            </li>
          </ol>
        </div>

        <div
          v-if="verificationTag"
          class="verification"
          :class="`is-${verificationTag.type}`"
        >
          <span class="verification__dot"></span>
          {{ verificationTag.text }}
        </div>
      </div>
    </div>
  </div>
</template>
