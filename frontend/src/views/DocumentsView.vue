<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
// main.js 把图标注册成了全局组件，模板里的 <UploadFilled /> 能用；
// 但 :icon="Refresh" 是表达式，只认 setup 作用域，必须显式导入
import { Refresh } from '@element-plus/icons-vue'
import {
  fetchDocuments,
  fetchIngestProgress,
  ingestDocuments,
  deleteDocument,
} from '../api/documents'

const documents = ref([])
const loading = ref(false)
const uploading = ref(false)
const fileList = ref([])
const uploadRef = ref(null)
// 当前正在轮询的入库任务进度，null 表示没有进行中的任务
const jobProgress = ref(null)

// 入库是异步的（POST /ingest 只登记任务就返回），所以只能轮询进度。
// 解析大文件可能很久，但不能无限等下去，超过上限就交给用户手动刷新。
const POLL_INTERVAL_MS = 1500
const POLL_TIMEOUT_MS = 30 * 60 * 1000

const progressPercent = computed(() => {
  const p = jobProgress.value
  if (!p || !p.total) return 0
  return Math.round(((p.done + p.failed) / p.total) * 100)
})

/** 轮询到任务结束为止。超时返回 null，由调用方提示用户手动刷新。 */
async function waitForJob(jobId) {
  const deadline = Date.now() + POLL_TIMEOUT_MS
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
    const progress = await fetchIngestProgress(jobId)
    jobProgress.value = progress
    if (progress.status !== 'running') return progress
  }
  return null
}

async function loadDocuments() {
  loading.value = true
  try {
    documents.value = await fetchDocuments()
  } catch (err) {
    ElMessage.error(`加载文档失败：${err.message}`)
  } finally {
    loading.value = false
  }
}

// el-upload 用 auto-upload=false，只把选中的文件攒起来，点“开始上传”再一次性提交
async function handleUpload() {
  if (fileList.value.length === 0) {
    ElMessage.warning('请先选择要上传的文件')
    return
  }

  uploading.value = true
  let jobId

  try {
    // 这一步只完成落盘和登记，后端立刻返回；真正干活的是 worker 进程
    const submitted = await ingestDocuments(fileList.value.map((f) => f.raw))
    jobId = submitted.job_id
    fileList.value = []
    uploadRef.value?.clearFiles()
    ElMessage.info(`已提交 ${submitted.total} 个文件，正在后台解析…`)
  } catch (err) {
    ElMessage.error(`上传失败：${err.message}`)
    uploading.value = false
    return
  }

  try {
    const progress = await waitForJob(jobId)
    if (progress === null) {
      ElMessage.warning('任务仍在后台处理，稍后刷新列表查看结果')
    } else if (progress.failed > 0) {
      ElMessage.warning(
        `入库完成：${progress.done}/${progress.total} 成功，${progress.failed} 个失败`,
      )
    } else {
      ElMessage.success(`已入库 ${progress.done} 个文件`)
    }
  } catch (err) {
    // 轮询断了不代表入库失败，任务还在 worker 里跑
    ElMessage.warning(`进度查询中断（${err.message}），请稍后刷新列表`)
  } finally {
    uploading.value = false
    jobProgress.value = null
    await loadDocuments()
  }
}

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除文档「${row.filename}」及其向量数据吗？此操作不可恢复。`,
      '删除文档',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  try {
    await deleteDocument(row.doc_id)
    ElMessage.success('文档已删除')
    await loadDocuments()
  } catch (err) {
    ElMessage.error(`删除失败：${err.message}`)
  }
}

function formatTime(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
    date.getDate(),
  )} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

onMounted(loadDocuments)
</script>

<template>
  <div class="documents-page">
    <el-card shadow="never" class="documents-card">
      <template #header>
        <div class="documents-card__head">
          <span class="documents-card__title">上传文档</span>
          <span class="documents-card__tip">
            支持 docx / pdf / pptx / 图片等，上传后会自动解析、切块并写入向量库
          </span>
        </div>
      </template>

      <el-upload
        ref="uploadRef"
        v-model:file-list="fileList"
        drag
        multiple
        :auto-upload="false"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">
          把文件拖到这里，或<em>点击选择</em>
        </div>
      </el-upload>

      <div class="documents-actions">
        <el-button
          type="primary"
          :loading="uploading"
          :disabled="fileList.length === 0"
          @click="handleUpload"
        >
          开始上传（{{ fileList.length }}）
        </el-button>
      </div>

      <div v-if="jobProgress" class="documents-progress">
        <el-progress :percentage="progressPercent" :stroke-width="10" />
        <span class="documents-progress__text">
          正在后台处理 {{ jobProgress.done + jobProgress.failed }} /
          {{ jobProgress.total }}
          <template v-if="jobProgress.failed > 0">
            （{{ jobProgress.failed }} 个失败）
          </template>
        </span>
      </div>
    </el-card>

    <el-card shadow="never" class="documents-card">
      <template #header>
        <div class="documents-card__head">
          <span class="documents-card__title">文档列表</span>
          <el-button text :icon="Refresh" @click="loadDocuments">
            刷新
          </el-button>
        </div>
      </template>

      <el-table v-loading="loading" :data="documents">
        <el-table-column label="文件名" min-width="220">
          <template #default="{ row }">
            <el-tooltip :content="row.file_path" placement="top">
              <span class="documents-filename">{{ row.filename }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="file_type" label="类型" width="110" />
        <el-table-column label="上传时间" width="180">
          <template #default="{ row }">
            <span class="documents-time">{{ formatTime(row.upload_time) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center">
          <template #default="{ row }">
            <el-button type="danger" text @click="handleDelete(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-note">还没有上传任何文档</div>
        </template>
      </el-table>
    </el-card>
  </div>
</template>
