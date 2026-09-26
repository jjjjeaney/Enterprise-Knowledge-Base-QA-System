import asyncio
import os
import time

from loguru import logger
from db.vector_repository import store_documents_bulk
from ingestion.process import process_doc


async def ingest_doc(file_path:str,file_name:str,doc_id:str,chunk_size:int=1000,chunk_overlap:int=150,):
    start=time.perf_counter()
    logger.info("文档入库开始 filename={} doc_id={}", file_name, doc_id)

    chunk_texts=await process_doc(file_path,chunk_size,chunk_overlap)
    data=[
        {"content": text, "metadata": {"source": file_name, "chunk_index": i,'doc_id':doc_id}}
        for i, text in enumerate(chunk_texts)
    ]
    ids=await store_documents_bulk(data)
    chunks_num=len(ids)
    logger.info(
        "文档入库完成 filename={} chunks={} elapsed_ms={:.0f}",
        file_name,
        chunks_num,
        (time.perf_counter()-start)*1000,
    )

    return chunks_num



