import asyncio
import time
from loguru import logger
from retrieval.vector_retrieval import vector_retrieval
from retrieval.bm25_retrieval import get_bm25


def reciprocal_rank_fusion(bm25_hits,vector_hits,vector_k=20,bm25_k=20):
    logger.debug(
        "rrf 融合 bm25_hits={} vector_hits={} vector_k={} bm25_k={}",
        len(bm25_hits),
        len(vector_hits),
        vector_k,
        bm25_k,
    )
    scores= {}
    for rank,doc in enumerate(bm25_hits,start=1):
        doc_id= doc['id']
        scores[doc_id]=scores.get(doc_id, 0.0) + 1.0 / (bm25_k + rank)
    for rank,doc in enumerate(vector_hits,start=1):
        doc_id= doc['id']
        scores[doc_id]=scores.get(doc_id, 0.0) + 1.0 / (vector_k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])

async def hybrid_retrieval(question:str,vector_k:int=20,bm25_k=20,top_k:int=5):
    start=time.perf_counter()
    bm25=await get_bm25()
    vector_hits, bm25_hits = await asyncio.gather(
        vector_retrieval(question, vector_k),
        bm25.search(question, bm25_k)
    )
    fused = reciprocal_rank_fusion(
        bm25_hits,
        vector_hits,
        vector_k,
        bm25_k
    )

    id_to_doc = {}

    for doc in bm25_hits + vector_hits:
        id_to_doc[doc["id"]] = doc

    # 根据 RRF 排名获取完整文档
    results = []

    for doc_id, score in fused[:top_k]:
        doc = id_to_doc[doc_id]

        results.append({
            "id": doc_id,
            "content": doc["content"],
            "metadata": doc["metadata"],
            "score": score
        })
    logger.info(
        "混合检索完成 vector_k={} bm25_k={} top_k={} returned={} elapsed_ms={:.0f}",
        vector_k,
        bm25_k,
        top_k,
        len(results),
        (time.perf_counter()-start)*1000,
    )
    return results



