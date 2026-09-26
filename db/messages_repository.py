import asyncio

from sqlalchemy import func, select, delete,update

from db import get_session
from db.models import Message, Thread


async def add_message(
        thread_id:str,
        content:str,
        role:str,
        sources:list[str]|None=None,
        verification:bool|None=None
):
    message = Message(
        thread_id=thread_id,
        content=content,
        role=role,
        sources=sources,
        verification=verification
    )
    async with get_session() as session:
        session.add(message)
        await session.execute(
            update(Thread).where(Thread.id == thread_id).values(updated_at=func.now())
        )
        await session.flush()
        return message.id

async def get_threads():
    stmt=(select(Thread).order_by(Thread.updated_at.desc()))
    async with get_session() as session:
        results = await session.execute(stmt)
        threads = results.scalars().all()
        return [
        {
        'id': thread.id,
        'title': thread.title,
        'created_at': thread.created_at.isoformat() if thread.created_at else None,
        'updated_at': thread.updated_at.isoformat() if thread.updated_at else None,
        }
        for thread in threads
            ]
async def get_messages(thread_id:str):
    stmt = (
        select(Message)
        .where(Message.thread_id == thread_id)
        .order_by(Message.created_at.asc())
    )
    async with get_session() as session:
        result = await session.execute(stmt)
        messages = result.scalars().all()
        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "sources": m.sources or [],
                "verification": m.verification,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]
async def create_thread(title:str):
    thread = Thread(
        title=title,
    )
    async with get_session() as session:
        session.add(thread)
        await session.flush()
        return thread.id

async def delete_thread(thread_id: str):
    async with get_session() as session:
        message_result = await session.execute(
            delete(Message).where(Message.thread_id == thread_id)
        )

        thread_result = await session.execute(
            delete(Thread).where(Thread.id == thread_id)
        )
        await session.commit()
    return {
            "messages_deleted": message_result.rowcount,
            "thread_deleted": thread_result.rowcount,
        }

async def rename_thread(thread_id:str, new_title:str):
    async with get_session() as session:
        stmt=update(Thread).where(Thread.id == thread_id).values(title=new_title,updated_at=func.now())
        result=await session.execute(stmt)
        return {'thread_changed':result.rowcount}


