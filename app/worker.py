"""ingest 后台 worker：领活 → 解析 → 写向量 → 回写状态。

必须独立于 API 进程运行。Docling 解析是 CPU 密集的同步调用，放在 API 进程里
会把整个事件循环连同所有接口一起卡住。

    python -m app.worker

要提并发就**多起几个同样的进程**：每个进程各自领活，靠 claim 里的
FOR UPDATE SKIP LOCKED 保证不会领到同一个文件，进程之间不需要任何协调。

解析的并行度只来自进程数。Docling 是 CPU + GIL 密集的，同一个进程里再开协程
也压不出并行，只会互相抢 GIL 并让心跳饿死——所以这里一次只处理一篇文档。
每个进程一份 Docling 模型（约 1.5–3GB 常驻），**进程数的真实上限是内存不是 CPU**。
"""
import os

from db import get_milvus_client

# 必须在 import torch / docling 之前设好。不设的话每个 worker 进程都会按核数
# 开满线程，N 个进程互相抢 CPU，总吞吐反而低于串行——多进程的收益会归零。
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

# 导入即生效：统一日志级别与格式（含行尾关联字段）
from app.logging_config import logger
from db.document_repository import add_document_if_absent
from db.ingest_job_repository import (
    claim_items,
    mark_done,
    mark_failed,
    new_worker_id,
    release_for_retry,
    touch_lease,
)
from db.models import Document
from core.config import (
    INGEST_HEARTBEAT_SECONDS,
    INGEST_LEASE_MINUTES,
    INGEST_MAX_ATTEMPTS,
    INGEST_POLL_SECONDS,
    UPLOAD_ROOT,
)
from ingestion import get_docling_converter
from ingestion.ingest_doc import ingest_doc


def _doc_id_for(content_hash: str) -> str:
    """doc_id 由文件内容推导，而不是随机生成。

    这样同一份文件被处理两次（重试、重复上传）会落到同一个 doc_id 上，
    documents 行的去重和 Milvus 主键的覆盖都靠这个成立。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, content_hash))


# 删除前用来确认路径确实落在 uploads/ 内。带尾部分隔符，
# 否则 uploads-old/ 这种前缀相同、实际在别处的路径会蒙混过关。
_UPLOAD_ROOT_PREFIX = os.path.realpath(UPLOAD_ROOT) + os.sep


def _remove_item_file(stored_path: str) -> None:
    """入库成功后删掉这份源文件，目录空了就一并删掉。

    stored_path 来自数据库，所以删之前先确认它真的在 uploads/ 之内——万一这一列
    被写坏或污染，这里不能变成删任意文件的入口。

    一个 job 可能含多个文件，目录是 per-job 的，所以别的文件还没处理完时 rmdir
    会失败，这是正常情况，忽略即可。

    这个函数**不抛异常**：它跑在入库成功之后，异常抛出去会把一条已完成的记录
    打回重试，白白重解析一遍。
    """
    try:
        path = os.path.realpath(stored_path)
        if not path.startswith(_UPLOAD_ROOT_PREFIX):
            logger.warning("stored_path 不在 uploads 目录内，拒绝删除 path={}", stored_path)
            return

        try:
            os.remove(path)
        except FileNotFoundError:
            pass  # 已经不在了（重复清理，或者被人工删过）

        try:
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass  # 目录非空（job 里还有别的文件在处理），或已被别的 worker 删掉
    except Exception:
        logger.exception("清理源文件出错 path={}", stored_path)


class _Lease:
    """心跳与主流程之间的共享状态。"""

    def __init__(self) -> None:
        self.stop = asyncio.Event()
        self.lost = False  # 租约被别的 worker 抢走时置位


async def _heartbeat(item_id, worker_id: str, lease: _Lease) -> None:
    """长任务定期续租，别让正在干的活被当成僵尸回收掉。

    这能生效的前提是事件循环没被同步调用堵死——所以 Docling 的
    converter.convert 走 asyncio.to_thread。如果哪天有人在主流程里加了
    阻塞调用，心跳会静默停摆，租约到期后这份文件会被重复领取。
    """
    while True:
        try:
            await asyncio.wait_for(
                lease.stop.wait(), timeout=INGEST_HEARTBEAT_SECONDS
            )
            return  # 主流程已经结束
        except asyncio.TimeoutError:
            pass  # 到期一次，续租

        try:
            if not await touch_lease(item_id, worker_id):
                lease.lost = True
                logger.warning("租约已被其它 worker 接管，放弃回写")
                return
        except Exception:
            logger.exception("续租失败")


async def _process_item(item: dict, worker_id: str) -> None:
    item_id = item["id"]
    filename = item["filename"]
    stored_path = item["stored_path"]

    # 绑一次，覆盖这条记录的全链路：心跳、ingest_doc → process_doc → 写向量。
    # 心跳任务在这个块里创建，create_task 会复制当前上下文，它的日志同样带这两个字段。
    with logger.contextualize(worker_id=worker_id, item_id=item_id):
        lease = _Lease()
        hb = asyncio.create_task(_heartbeat(item_id, worker_id, lease))
        ingested = False

        try:
            doc_id = _doc_id_for(item["content_hash"])
            logger.info("开始处理 filename={} attempt={}", filename, item["attempts"])

            chunk_count = await ingest_doc(stored_path, file_name=filename,doc_id=doc_id)

            if lease.lost:
                # 已经有别的 worker 接手，这时候回写会把对方的结果覆盖掉
                return

            # 向量写成功之后再落 documents 行，避免出现"列表里有、检索不到"的孤立记录
            await add_document_if_absent(Document(
                doc_id=doc_id,
                filename=filename,
                file_path=stored_path,
                file_type=Path(filename).suffix.lower(),
                upload_time=datetime.now(),
            ))
            await mark_done(item_id, doc_id, chunk_count)
            ingested = True
            logger.info(
                "入库完成 filename={} chunks={} doc_id={}", filename, chunk_count, doc_id
            )

        except Exception as exc:
            logger.exception("入库失败 filename={}", filename)
            if lease.lost:
                return
            try:
                if item["attempts"] >= INGEST_MAX_ATTEMPTS:
                    await mark_failed(item_id, repr(exc))
                else:
                    await release_for_retry(item_id, repr(exc))
            except Exception:
                logger.exception("回写失败状态时出错")

        finally:
            lease.stop.set()
            await asyncio.gather(hb, return_exceptions=True)

        # 刻意放在重试逻辑之外：清理失败绝不能把一条已经入库成功的记录打回重试。
        # 反过来，租约被抢走时上面会提前 return，这里也就不会去动别人的文件。
        if ingested:
            _remove_item_file(stored_path)


async def worker_loop(worker_id: str) -> None:
    with logger.contextualize(worker_id=worker_id):
        logger.info(
            "worker 启动 lease_min={} heartbeat_s={}",
            INGEST_LEASE_MINUTES,
            INGEST_HEARTBEAT_SECONDS,
        )

        while True:
            try:
                items = await claim_items(worker_id, batch=1)
            except Exception:
                # 数据库抖一下不该让整个 worker 退出，等下一轮
                logger.exception("领取任务失败，{}s 后重试", INGEST_POLL_SECONDS)
                await asyncio.sleep(INGEST_POLL_SECONDS)
                continue

            if not items:
                await asyncio.sleep(INGEST_POLL_SECONDS)
                continue

            await _process_item(items[0], worker_id)


def main() -> None:
    worker_id = new_worker_id()

    if INGEST_HEARTBEAT_SECONDS * 2 >= INGEST_LEASE_MINUTES * 60:
        logger.warning(
            "心跳间隔相对租约偏大，解析慢的文件有被重复领取的风险 "
            "heartbeat_s={} lease_min={}",
            INGEST_HEARTBEAT_SECONDS,
            INGEST_LEASE_MINUTES,
        )

    # 提前把 Docling 模型加载好：冷启动要几秒到十几秒，提前做掉可以让第一篇文档
    # 不必承担这个代价，也让配置和依赖问题在启动时就暴露，而不是等第一篇文档才炸。
    get_docling_converter()
    get_milvus_client()
    try:
        asyncio.run(worker_loop(worker_id))
    except KeyboardInterrupt:
        logger.info(
            "收到中断信号退出，正在处理的那条记录状态仍是 running，"
            "租约到期后会被其它 worker 捡回 lease_min={}",
            INGEST_LEASE_MINUTES,
        )


if __name__ == "__main__":
    main()
