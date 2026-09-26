"""ingest 任务表的状态机：领活 / 续租 / 回写 / 进度。

请求侧（POST /ingest）只往表里写，worker 侧只从表里领，两边不直接通信。
领活用 PostgreSQL 的 FOR UPDATE SKIP LOCKED，所以可以安全地起任意多个 worker
进程——不需要调度器，也不需要独立的 reaper 进程。

状态流转：

    pending ──领活──▶ running ──成功──▶ done
       ▲                 │
       │                 ├──失败且还有额度──▶ pending
       └──(重试额度用尽)──┴──失败且额度用尽──▶ failed
"""
import os
import socket
from uuid import UUID
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from db import get_session
from db.models import IngestJob, IngestJobItem
from core.config import INGEST_LEASE_MINUTES, INGEST_MAX_ATTEMPTS


def new_worker_id() -> str:
    """进程级唯一标识——每个 worker 进程一个，互不覆盖。"""
    return f"{socket.gethostname()}-{os.getpid()}"


# 一条语句同时做三件事：挑出待处理的、挑出租约已过期的（前面领走它的 worker
# 崩了，这就是僵尸回收，折进 WHERE 条件里所以不需要独立的 reaper）、
# 原子地改成 running 并挂上自己的租约。
#
# FOR UPDATE SKIP LOCKED 让并发的 worker 各领各的：不会领到同一条，
# 也不会因为别人正锁着而阻塞等待。
_CLAIM_SQL = text("""
WITH claimed AS (
    SELECT id
    FROM ingest_job_items
    WHERE status = 'pending'
       OR (status = 'running'
           AND lease_expires_at < now()
           AND attempts < :max_attempts)
    ORDER BY updated_at
    LIMIT :batch
    FOR UPDATE SKIP LOCKED
)
UPDATE ingest_job_items i
SET status           = 'running',
    worker_id        = :worker_id,
    lease_expires_at = now() + CAST(:lease_minutes AS int) * interval '1 minute',
    attempts         = i.attempts + 1,
    updated_at       = now()
FROM claimed c
WHERE i.id = c.id
RETURNING i.id, i.job_id, i.filename, i.stored_path, i.content_hash, i.attempts
""")

# FOR UPDATE加行级锁，SKIP LOCKED跳过锁住的行，否则要等待

# 上面那条 SQL 的僵尸分支要求 attempts < max_attempts，所以"重试额度已经耗光、
# 租约又过期"的行谁也领不走，需要单独判死。否则它们会永远停在 running 上，
# 而且没有 worker 会去执行它们的失败分支。
_BURY_EXHAUSTED_SQL = text("""
UPDATE ingest_job_items
SET status           = 'failed',
    error            = COALESCE(error, '租约多次过期，放弃重试'),
    worker_id        = NULL,
    lease_expires_at = NULL,
    updated_at       = now()
WHERE status = 'running'
  AND lease_expires_at < now()
  AND attempts >= :max_attempts
RETURNING job_id
""")

# 把 job 的整体状态写回 ingest_jobs.status。这个列只是给"任务列表"这类场景
# 省一次 join 用的缓存；权威值仍以 get_job_progress 从 item 现算的为准。
_REFRESH_JOB_STATUS_SQL = text("""
UPDATE ingest_jobs j
SET status      = agg.status,
    finished_at = CASE WHEN agg.status = 'running' THEN NULL ELSE now() END
FROM (
    SELECT CAST(:job_id AS uuid) AS job_id,
           CASE
               WHEN count(*) FILTER (WHERE status IN ('pending', 'running')) > 0
                   THEN 'running'
               WHEN count(*) FILTER (WHERE status = 'failed') > 0
                   THEN 'partial'
               ELSE 'succeeded'
           END AS status
    FROM ingest_job_items
    WHERE job_id = CAST(:job_id AS uuid)
) agg
WHERE j.id = agg.job_id
""")


async def _refresh_job_status(session: AsyncSession, job_id: UUID) -> None:
    """重算并写回某个 job 的整体状态。

    必须先拿到 job 行的排他锁再重算。否则两个 worker 可能各自基于过期的 item
    快照算出互相矛盾的结论，后写的那个把已经终态的 job 又写回 running——而此后
    不会再有 item 变化来纠正它，错误就永久留在那里了。

    加了锁之后并发的重算会排队，最后执行的那次必然看得到所有已提交的 item
    状态，写出的也就一定是最终结论。
    """
    await session.execute(
        text("SELECT id FROM ingest_jobs WHERE id = :j FOR UPDATE"), {"j": job_id}
    )
    await session.execute(_REFRESH_JOB_STATUS_SQL, {"job_id": job_id})


async def claim_items(worker_id: str, batch: int = 1) -> list[dict]:
    """领一批待处理的文件。多个 worker 同时调用也不会领到同一条。"""
    async with get_session() as session:
        buried = await session.execute(
            _BURY_EXHAUSTED_SQL, {"max_attempts": INGEST_MAX_ATTEMPTS}
        )
        # 被判死的这些 item 没有走 _set_status，所以要在这里补一次 job 状态重算，
        # 否则如果它们是 job 里最后落定的几条，那个 job 就永远停在 running 上。
        # 正常情况下这里是空的，不带来额外开销。
        for job_id in {row[0] for row in buried.all()}:
            await _refresh_job_status(session, job_id)

        result = await session.execute(_CLAIM_SQL, {
            "worker_id": worker_id,
            "batch": batch,
            "max_attempts": INGEST_MAX_ATTEMPTS,
            "lease_minutes": INGEST_LEASE_MINUTES,
        })
        return [dict(row) for row in result.mappings().all()]


async def _set_status(item_id: UUID, status: str, **fields) -> None:
    async with get_session() as session:
        updated = await session.execute(
            update(IngestJobItem)
            .where(IngestJobItem.id == item_id)
            .values(
                status=status,
                # 处理已经有结论了，清掉租约，免得别的 worker 还要去判断它是不是僵尸
                lease_expires_at=None,
                worker_id=None,
                updated_at=func.now(),
                **fields,
            )
            .returning(IngestJobItem.job_id)
        )
        # 和 item 的状态变更放在同一个事务里提交：这样"最后一条 item 落定"和
        # "job 被标成终态"要么一起生效、要么一起不生效，中间没有窗口。
        job_id = updated.scalar_one_or_none()
        if job_id is not None:
            await _refresh_job_status(session, job_id)


async def mark_done(item_id: UUID, doc_id: str, chunk_count: int) -> None:
    await _set_status(
        item_id, "done", doc_id=doc_id, chunk_count=chunk_count, error=None
    )


async def mark_failed(item_id: UUID, error: str) -> None:
    await _set_status(item_id, "failed", error=error[:2000])


async def release_for_retry(item_id: UUID, error: str) -> None:
    """还有重试额度：退回 pending，等下一轮被重新领走。"""
    await _set_status(item_id, "pending", error=error[:2000])


async def touch_lease(item_id: UUID, worker_id: str) -> bool:
    """续租。返回 False 表示租约已经不在自己手里了。

    WHERE 里带上 worker_id 是关键：万一租约真被别的 worker 抢走，
    这里会拿到 rowcount == 0，调用方就能及时停手，
    而不是闷头把活干完再把对方的结果覆盖掉。
    """
    async with get_session() as session:
        result = await session.execute(
            text("""
                UPDATE ingest_job_items
                SET lease_expires_at =
                        now() + CAST(:mins AS int) * interval '1 minute',
                    updated_at = now()
                WHERE id = :item_id
                  AND status = 'running'
                  AND worker_id = :worker_id
            """),
            {
                "item_id": item_id,
                "worker_id": worker_id,
                "mins": INGEST_LEASE_MINUTES,
            },
        )
        return result.rowcount > 0


async def get_job(job_id: UUID) -> IngestJob | None:
    async with get_session() as session:
        return await session.get(IngestJob, job_id)


async def get_job_progress(job_id: UUID, error_limit: int = 50) -> dict:
    """
    从job_item表中获取任务进度，
    进度直接从 item 状态聚合出来，不维护计数器——多进程下计数器必然算错。

    """
    counts_stmt = (
        select(IngestJobItem.status, func.count())
        .where(IngestJobItem.job_id == job_id)
        .group_by(IngestJobItem.status)
    )
    errors_stmt = (
        select(IngestJobItem.filename, IngestJobItem.error)
        .where(IngestJobItem.job_id == job_id, IngestJobItem.status == "failed")
        .limit(error_limit)
    )

    async with get_session() as session:
        counts = {s: n for s, n in (await session.execute(counts_stmt)).all()}
        errors = [
            {"filename": name, "error": err}
            for name, err in (await session.execute(errors_stmt)).all()
        ]

    total = sum(counts.values())
    failed = counts.get("failed", 0)
    if counts.get("done", 0) + failed < total:
        status = "running"
    elif failed:
        status = "partial"
    else:
        status = "succeeded"

    return {
        "job_id": str(job_id),
        "status": status,
        "total": total,
        "done": counts.get("done", 0),
        "failed": failed,
        "pending": counts.get("pending", 0),
        "running": counts.get("running", 0),
        "errors": errors,
    }
