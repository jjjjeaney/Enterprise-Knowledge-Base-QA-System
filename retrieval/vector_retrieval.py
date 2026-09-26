import time
from typing import List, Any
from loguru import logger
from db.vector_repository import query_doc


async def vector_retrieval(question:str,k:int)->List[dict[str,Any]]:
    start=time.perf_counter()
    query_result=await query_doc(question,k)
    elapsed=time.perf_counter()-start
    logger.debug(
        "向量检索完成 requested_k={} returned={} elapsed_ms={:.0f}",
        k,
        len(query_result),
        elapsed*1000,
    )

    return [
        {
        'id':item.id,
        'content':item.content,
        'metadata':item.metadata
        }
        for item in query_result
    ]


