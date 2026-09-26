import client from './client'

/** 文档列表：[{ doc_id, filename, file_path, file_type, upload_time }] */
export async function fetchDocuments() {
  const { data } = await client.get('/get_documents')
  return data
}

/**
 * 上传文件，创建一个异步的入库任务。
 * 后端立刻就返回 202（只完成了落盘和登记），解析与写向量在 worker 进程里进行，
 * 所以这里拿不到片段数，要等轮询 fetchIngestProgress 才知道结果。
 * 返回：{ job_id, total }
 */
export async function ingestDocuments(files) {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }
  // 不要手动设置 Content-Type，交给 axios 自动带上 multipart 的 boundary
  const { data } = await client.post('/ingest', formData)
  return data
}

/**
 * 查询入库任务进度，配合 ingestDocuments 轮询使用。
 * 返回：{ job_id, status, total, done, failed, pending, running, errors }
 * status 为 'running' 表示还没处理完，'succeeded' / 'partial' 表示已结束
 * （partial 指有一部分文件失败）。
 */
export async function fetchIngestProgress(jobId) {
  const { data } = await client.get(`/ingest_jobs/${jobId}`)
  return data
}

/** 删除文档（同时删 PostgreSQL 元数据与 Milvus 向量） */
export async function deleteDocument(docId) {
  const { data } = await client.delete('/delete_document', {
    params: { id: docId },
  })
  return data
}
