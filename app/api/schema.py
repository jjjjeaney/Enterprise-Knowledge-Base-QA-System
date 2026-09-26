from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    thread_id:str|None=None
    question:str


class ChatResponse(BaseModel):
    question: str
    answer: str
    sources: list[str]
    tool_used: Optional[str] = None
    verification: bool|None = None
    thread_id: str
    rag_context: str


class IngestResponse(BaseModel):
    filename: str
    chunks_ingested: int

class ThreadResponse(BaseModel):
    thread_id: str
    title: str

class ThreadRequest(BaseModel):
    thread_id: str

class GetMessagesResponse(BaseModel):
    id:str
    role: str
    content: str
    sources: list[str]
    verification: bool|None=None
    created_at:str


# 显示会话列表
class ThreadSummary(BaseModel):
    id: str
    title: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

