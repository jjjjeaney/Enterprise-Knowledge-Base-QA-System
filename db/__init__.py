from contextlib import asynccontextmanager
from functools import lru_cache
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from pymilvus import MilvusClient
from core.config import POSTGRESQL_URL, MILVUS_URI

engine=create_async_engine(url=POSTGRESQL_URL,echo=True)
AsyncSessionLocal=async_sessionmaker(bind=engine,expire_on_commit=False)

@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
@lru_cache
def get_milvus_client()->MilvusClient:
    client = MilvusClient(MILVUS_URI)
    return client



class Base(DeclarativeBase):
    pass







