import client from './client'

/**
 * 提问。
 * 注意：新建会话时必须显式传 thread_id: null，否则后端返回 422。
 * 返回值形如：
 *   { question, answer, sources, tool_used, verification, thread_id, rag_context }
 */
export async function sendChat(threadId, question) {
  const { data } = await client.post('/chat', {
    thread_id: threadId ?? null,
    question,
  })
  return data
}

/** 会话列表：[{ id, title, created_at, updated_at }] */
export async function fetchThreads() {
  const { data } = await client.get('/get_threads')
  return data
}

/** 某个会话的历史消息：[{ id, role, content, sources, verification, created_at }] */
export async function fetchThreadMessages(threadId) {
  const { data } = await client.get('/get_thread_messages', {
    params: { id: threadId },
  })
  return data
}

/** 删除会话 */
export async function deleteThread(threadId) {
  const { data } = await client.delete('/delete_thread', {
    params: { id: threadId },
  })
  return data
}

/** 重命名会话（id 与 new_title 都是查询参数） */
export async function renameThread(threadId, newTitle) {
  const { data } = await client.post('/rename_thread', null, {
    params: { id: threadId, new_title: newTitle },
  })
  return data
}
