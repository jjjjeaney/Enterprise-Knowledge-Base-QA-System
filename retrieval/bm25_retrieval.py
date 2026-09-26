import asyncio
import time
from typing import Any
from loguru import logger
import jieba
from pymilvus import MilvusClient
from rank_bm25 import BM25Okapi

from core.config import MILVUS_DB, MILVUS_COLLECTION, MILVUS_URI
from db.document_repository import get_corpus_version

remove_list=set()

# 初始化检索器
class BM25retriever:
    def __init__(self, corpus: list[dict[str, Any]]):
        self.contents = [item["content"] for item in corpus]
        self.ids = [item["id"] for item in corpus]
        self.metadatas = [item["metadata"] for item in corpus]

        self.tokenized_texts = self.get_tokenized_texts(self.contents)
        self.bm25 = BM25Okapi(self.tokenized_texts)


    # 将文本分词
    def get_tokenized_texts(self,texts):
        tokenized_texts=[[word for word in jieba.cut(text) if word not in remove_list] for text in texts]
        return tokenized_texts

    async def search(self,question:str,top_k:int):
        start=time.perf_counter()
        tokenized_question=[word for word in jieba.cut(question) if word not in remove_list]
        scores=self.bm25.get_scores(tokenized_question)
        top_indices = sorted(
            range(len(scores)),
            key=lambda i: scores[i],
            reverse=True
        )[:top_k]
        elapsed=time.perf_counter()-start
        logger.debug(
            "bm25 检索完成 requested_k={} corpus={} elapsed_ms={:.0f}",
            top_k,
            len(self.ids),
            elapsed*1000,
        )
        return [
            {
                'id': self.ids[i],
                'content': self.contents[i],
                'metadata':self.metadatas[i],
            }
            for i in top_indices
        ]



_index: "BM25retriever | None" = None
# 建上面这份索引时读到的语料指纹，用来判断索引还能不能接着用
_index_version: str | None = None
# 重建很慢（全量扫 Milvus + 重建 BM25），多个请求同时发现语料变了时只让一个去建，
# 其它在锁上等它建完直接复用
_index_lock = asyncio.Lock()


def _build_index() -> BM25retriever:
    """全量扫描 Milvus 建一份新索引。

    同步且慢，只从 get_bm25 的 asyncio.to_thread 里调用——直接在事件循环上跑
    会把所有并发请求一起卡住。
    """
    client = MilvusClient(MILVUS_URI)
    client.use_database(MILVUS_DB)
    query_result_iterator=client.query_iterator(
        collection_name=MILVUS_COLLECTION,
        batch_size=1000,
        filter="",  # 不加过滤 = 扫全部
        output_fields=['id','content','metadata'],
    )
    logger.debug("开始全量扫描 Milvus 重建 bm25 索引")
    start=time.perf_counter()
    all_chunks=[]
    while True:
        items=query_result_iterator.next()
        if not items:
            break
        for item in items:
            all_chunks.append({'id':item['id'],'content':item['content'],'metadata':item['metadata']})
    query_result_iterator.close()
    bm25=BM25retriever(all_chunks)
    logger.info(
        "bm25 索引重建完成 chunks={} elapsed_ms={:.0f}",
        len(all_chunks),
        (time.perf_counter()-start)*1000,
    )
    return bm25


async def get_bm25() -> BM25retriever:
    """取当前的 BM25 索引；语料变了就重建后返回新的。

    索引存在本进程内存里，而入库跑在 worker 进程，跨进程没有通知通道，所以这里
    用"每次检索前读一次语料指纹"来代替推送：指纹和自己建索引时记下的一致就直接
    返回旧索引（代价是一条很小的查询），不一致就重建。因为指纹在每篇文档写成功
    后都会变，一个 ingest 任务处理完之后的下一次检索就会带上新文档。

    指纹必须在**开始扫描 Milvus 之前**读取。若在扫描之后才读，扫的过程中新写的
    文档会被记进指纹里、却不在刚建好的索引中，那份索引就永远漏掉它了。先读再扫
    的代价只是"扫描期间新增的文档要等下一次重建"，不会丢。
    """
    global _index, _index_version

    if _index is not None and await get_corpus_version() == _index_version:
        return _index

    async with _index_lock:
        # 等锁期间可能已经有别的请求重建过了，重新读一次指纹再决定
        version = await get_corpus_version()
        if _index is not None and version == _index_version:
            return _index

        logger.info(
            "语料指纹变化，重建 bm25 索引 version_before={} version_after={}",
            _index_version,
            version,
        )
        index = await asyncio.to_thread(_build_index)
        _index = index
        _index_version = version
        return _index



