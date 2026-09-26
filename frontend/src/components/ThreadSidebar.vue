<script setup>
import { onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useChatStore } from '../stores/chat'

const store = useChatStore()

function formatTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

async function handleDelete(thread) {
  try {
    await ElMessageBox.confirm('确定删除该会话及其全部消息吗？', '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  try {
    await store.removeThread(thread.id)
    ElMessage.success('会话已删除')
  } catch (err) {
    ElMessage.error(`删除失败：${err.message}`)
  }
}

async function handleRename(thread) {
  let newTitle
  try {
    const res = await ElMessageBox.prompt('请输入新的会话名称', '重命名会话', {
      inputValue: thread.title || '',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (value) => (value && value.trim() ? true : '名称不能为空'),
    })
    newTitle = res.value.trim()
  } catch {
    return // 用户取消
  }
  try {
    await store.rename(thread.id, newTitle)
    ElMessage.success('会话已重命名')
  } catch (err) {
    ElMessage.error(`重命名失败：${err.message}`)
  }
}

onMounted(() => {
  store.loadThreads().catch((err) => ElMessage.error(`加载会话失败：${err.message}`))
})
</script>

<template>
  <aside class="thread-sidebar">
    <div class="thread-sidebar__head">
      <el-button type="primary" style="width: 100%" @click="store.startNewThread()">
        <el-icon><Plus /></el-icon>
        <span>新会话</span>
      </el-button>
    </div>

    <div v-loading="store.loadingThreads" class="thread-list">
      <div
        v-if="!store.loadingThreads && store.threads.length === 0"
        class="empty-note"
      >
        还没有会话
      </div>
      <div
        v-for="thread in store.threads"
        :key="thread.id"
        class="thread-item"
        :class="{ 'is-active': thread.id === store.currentThreadId }"
        @click="store.openThread(thread.id)"
      >
        <el-icon><ChatDotRound /></el-icon>
        <div class="thread-item__body">
          <div class="thread-item__title">{{ thread.title || '未命名会话' }}</div>
          <div class="thread-item__time">{{ formatTime(thread.updated_at) }}</div>
        </div>
        <div class="thread-item__actions">
          <el-icon :size="18" title="重命名" @click.stop="handleRename(thread)">
            <EditPen />
          </el-icon>
          <el-icon :size="18" title="删除" @click.stop="handleDelete(thread)">
            <Delete />
          </el-icon>
        </div>
      </div>
    </div>
  </aside>
</template>
