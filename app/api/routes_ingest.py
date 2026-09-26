import hashlib
import os
import re
import shutil
import uuid
from uuid import UUID

import aiofiles
from fastapi import APIRouter, UploadFile, File, HTTPException
from loguru import logger

from db import get_session
from db.ingest_job_repository import get_job, get_job_progress
from db.models import IngestJobItem, IngestJob
from core.config import UPLOAD_ROOT

router = APIRouter()

CHUNK_SIZE = 1024 * 1024

# 扩展名会被拼进存储路径，只接受这种最朴素的形态
_EXT_PATTERN = re.compile(r'^\.[A-Za-z0-9]{1,10}$')


def _safe_ext(filename: str) -> str:
    """取出可安全用于路径的扩展名，取不到就返回空串。"""
    ext = os.path.splitext(filename)[1]
    if not _EXT_PATTERN.match(ext):
        return ''
    # 统一小写：process.py 判断图片后缀时区分大小写，.JPG/.PDF 会走错分支
    return ext.lower()


async def save_to_uploads(job_id: uuid.UUID, upload: UploadFile) -> tuple[str, str]:
    """流式落盘，返回 (存储路径, content_hash)。

    分块读写，不把整个文件读进内存；哈希在写入过程中增量算出，
    不需要再扫一遍文件。路径用 uuid 生成，与原始文件名无关，
    因此同名文件不会互相覆盖，也不存在路径穿越。
    """
    dir_path = os.path.join(UPLOAD_ROOT, str(job_id))
    os.makedirs(dir_path, exist_ok=True)

    path = os.path.join(
        dir_path,
        f'{uuid.uuid4().hex}{_safe_ext(upload.filename or "")}',
    )
    # 获取一个sha256计算器
    hasher = hashlib.sha256()

    async with aiofiles.open(path, 'wb') as f:
        while chunk := await upload.read(CHUNK_SIZE):
            # 持续向hasher计算器中添加数据，最后再计算hash值
            hasher.update(chunk)
            await f.write(chunk)

    return path, hasher.hexdigest()


@router.post("/ingest", status_code=202)
async def ingest(files: list[UploadFile] = File(...)):
    job_id = uuid.uuid4()
    job_dir = os.path.join(UPLOAD_ROOT, str(job_id))
    items = []

    # 整个请求都挂在这个 job_id 下：落盘失败时抛出的异常、以及后续任何新增日志，
    # 行尾都会带上它，和 worker 那边同一条记录的日志能对上号。
    with logger.contextualize(job_id=job_id):
        try:
            for upload in files:
                original_name = os.path.basename(upload.filename or '')
                path, content_hash = await save_to_uploads(job_id, upload)   # ① 流式落盘
                items.append(IngestJobItem(
                    job_id=job_id,
                    filename=original_name,
                    stored_path=path,
                    content_hash=content_hash,                             # ② 写入时顺手算出
                ))

            async with get_session() as session:                       # ③ 一个事务写全部
                session.add(IngestJob(id=job_id, total=len(items)))
                session.add_all(items)
        except Exception:
            # 落盘或写库失败时清掉整个 job 目录，不留半份文件
            shutil.rmtree(job_dir, ignore_errors=True)
            raise

        logger.info("入库任务已创建 total={}", len(items))

        # await notify("ingest_new")                                 # 可选：唤醒 worker
        return {"job_id": job_id, "total": len(items)}             # 202 立即返回


@router.get("/ingest_jobs/{job_id}")
async def get_ingest_job(job_id: UUID):
    """查询入库任务进度，供前端轮询。

    POST /ingest 是异步的：它只登记任务就返回，真正的解析和写向量发生在 worker
    进程里，所以调用方只能靠轮询这个接口判断什么时候算完。
    """


    if await get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    return await get_job_progress(job_id)


