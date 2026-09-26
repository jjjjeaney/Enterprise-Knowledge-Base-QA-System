import asyncio
import datetime
import time
import uuid
from typing import List, Dict, Any
from db import get_milvus_client
from db.models import DocumentChunk
from core.config import MILVUS_COLLECTION, MILVUS_DB
from core.embedding import get_embedding
from loguru import logger


def _chunk_id(metadata: dict) -> str:
    """由 doc_id + chunk_index 推导 chunk 主键。

    doc_id 来自文件内容哈希，所以同一份文件重跑会得到同一批主键，
    Milvus 的 upsert 才是真的"原地覆盖"，而不是又写进去一份。
    随机主键会让 upsert 退化成纯 insert，worker 的重试就变成了灌重复数据。
    """
    doc_id = metadata.get("doc_id")
    chunk_index = metadata.get("chunk_index")
    if doc_id is None or chunk_index is None:
        raise ValueError(f"chunk metadata 缺少 doc_id / chunk_index：{metadata!r}")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{chunk_index}"))


async def store_documents_bulk(content_metadata: List[Dict[str, Any]]) -> List[str]:
    embedding_model=get_embedding()
    milvus_client=get_milvus_client()
    texts=[item['content'] for item in content_metadata]
    # 向量化和 Milvus 写入都是同步阻塞调用。直接调用会把事件循环连同 worker 的
    # 续租心跳一起堵死，所以都挪到线程里跑。
    vectors=await asyncio.to_thread(embedding_model.embed_documents, texts)

    data=[
        {
        'id':_chunk_id(item['metadata']),
        'content':item['content'],
        'metadata':item['metadata'],
        'vector':vectors[i],
        'created_at':datetime.datetime.now(),
    }
        for i,item in enumerate(content_metadata)
    ]
    milvus_client.use_database(MILVUS_DB)
    await asyncio.to_thread(
        milvus_client.upsert, collection_name=MILVUS_COLLECTION, data=data
    )
    ids=[item['id'] for item in data]

    return ids


async def query_doc(query:str,k) -> List[DocumentChunk]:
    client=get_milvus_client()
    client.use_database(MILVUS_DB)
    embedding_model=get_embedding()
    start=time.perf_counter()
    query_vector=embedding_model.embed_query(query)
    elapsed=time.perf_counter()-start
    logger.debug("embed_query 完成 elapsed_ms={:.0f}", elapsed*1000)
    start = time.perf_counter()
    results = client.search(
        collection_name=MILVUS_COLLECTION,
        data=[query_vector],  # Milvus search 这里仍然要传二维列表
        limit=k,
        output_fields=['*']
    )
    elapsed=time.perf_counter()-start
    logger.debug("milvus search 完成 requested_k={} elapsed_ms={:.0f}", k, elapsed*1000)
    documents=[]
    for hit in results[0]:
        documents.append(DocumentChunk(
            id=hit['id'],
            content=hit['content'],
            vector=hit['vector'],
            metadata=hit['metadata'],
            created_at=hit['created_at'],
        ))
    return documents

async def remove_doc_chunk(doc_id:str):
    client=get_milvus_client()
    client.use_database(MILVUS_DB)
    result=client.delete(
        collection_name=MILVUS_COLLECTION,
        filter=f'metadata["doc_id"] == "{doc_id}"'
    )
    return result




