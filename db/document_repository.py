import asyncio

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from db import get_session
from db.models import Document


async def add_document(document:Document):
    async with get_session() as session:
        session.add(document)
        await session.flush()

        return document.doc_id


async def add_document_if_absent(document: Document):
    """doc_id 已存在就什么都不做。

    worker 里的 doc_id 由文件内容哈希推导，所以同一个文件被重复处理（重试、
    重复上传）会落到同一个 doc_id 上。没有这个 ON CONFLICT，第二次插入会撞
    主键、把一次本可以成功的重试变成永久失败。
    """
    async with get_session() as session:
        await session.execute(
            pg_insert(Document)
            .values(
                doc_id=document.doc_id,
                filename=document.filename,
                file_path=document.file_path,
                file_type=document.file_type,
                upload_time=document.upload_time,
            )
            .on_conflict_do_nothing(index_elements=["doc_id"])
        )
    return document.doc_id


async def delete_document_by_id(document_id:str):
    async with get_session() as session:
        result= await session.execute(delete(Document).where(Document.doc_id == document_id))
        await session.flush()
    return {'document_deleted':result.rowcount}

async def list_documents():
    stmt=select(Document).order_by(Document.upload_time)
    async with get_session() as session:
        result = await session.execute(stmt)
        documents = result.scalars().all()
        return [
            {
            'doc_id': d.doc_id,
                'filename': d.filename,
                'file_path': d.file_path,
                'file_type':d.file_type,
                'upload_time': d.upload_time
        }
           for d in documents
        ]


async def get_corpus_version() -> str:
    """语料指纹：documents 表一变，这个字符串就变。

    用来给 BM25 索引做失效判断。BM25 索引建在 API 进程的内存里，而入库发生在
    worker 进程，两边没有通知通道，所以 API 侧不去问"有没有新文档"，而是每次
    检索前读一次这个指纹，和自己建索引时记下的比对，不一致就重建——一个 ingest
    任务跑完（更准确地说：每篇文档写成功）之后指纹必然变化，下一个检索请求自然
    就会带上新文档。

    用 (行数, 最新 upload_time) 而不是单独的计数器，是为了同时覆盖两种变化：
    删文档靠行数变小体现；"删一篇又加一篇"这种行数抵消的情况靠 upload_time 变大
    体现。documents 行是 worker 在向量写成功之后才写的，所以指纹一变，Milvus 里
    确实就多了东西。

    重复上传同一份文件（内容哈希相同、doc_id 相同）不会写新行，指纹不变——这恰好
    对应"语料没变"，不需要重建。
    """
    stmt = select(func.count(Document.doc_id), func.max(Document.upload_time))
    async with get_session() as session:
        count, latest = (await session.execute(stmt)).one()
    return f"{count}:{latest.isoformat() if latest else ''}"


async def main():
    await delete_document_by_id('05876700-5b1c-40d6-970f-83140d76837d')

if __name__ == '__main__':
    result=asyncio.run(main())
    print(result)