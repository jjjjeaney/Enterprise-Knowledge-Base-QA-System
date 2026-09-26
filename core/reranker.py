from functools import lru_cache

from FlagEmbedding import FlagReranker

from core.config import RERANKER_MODEL_PATH


@lru_cache
def get_reranker() -> FlagReranker:
    """载入 bge rerank 模型，进程内只载一次。

    FlagReranker 载入很慢（读权重、上显存），所以用 lru_cache 兜住：
    第一次调用时才真正加载，之后所有调用复用同一个实例。用法和
    core.embedding.get_embedding 一致。

    注意返回的实例是线程安全的，但其 compute_score 是同步阻塞的，
    在事件循环里使用时要包一层 asyncio.to_thread。
    """
    return FlagReranker(
        RERANKER_MODEL_PATH,
        use_fp16=True,
        normalize=True,
    )
