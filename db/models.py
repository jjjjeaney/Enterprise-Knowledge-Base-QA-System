import uuid

import sqlalchemy
from sqlalchemy import String, DateTime, ForeignKey, Index, JSON, Text, func, Boolean, Integer, UUID
from sqlalchemy.orm import Mapped, mapped_column, foreign
from datetime import datetime
from pydantic import BaseModel
from db import Base

def _uuid():
    return str(uuid.uuid4())



class Thread(Base):
    """One chat conversation. Shown in the sidebar history list."""
    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    title: Mapped[str | None]=mapped_column(String,default="New chat",server_default="New chat")
    created_at: Mapped[datetime | None]=mapped_column(DateTime,default=datetime.now,server_default=func.now())
    updated_at: Mapped[datetime | None]=mapped_column(DateTime,default=datetime.now,server_default=func.now())

class Document(Base):
    __tablename__ = "documents"
    doc_id:Mapped[str]=mapped_column(String, primary_key=True, default=_uuid)
    filename:Mapped[str]=mapped_column(String)
    file_path:Mapped[str]=mapped_column(String)
    file_type:Mapped[str]=mapped_column(String)
    upload_time:Mapped[datetime | None]=mapped_column(DateTime,default=datetime.now,server_default=func.now())

class DocumentChunk(BaseModel):
    id: str
    content: str
    metadata:dict
    vector:list[float]
    created_at:str

class Message(Base):
    """用户或AI的一次消息"""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(String, ForeignKey("threads.id"), index=True)
    role: Mapped[str] = mapped_column(String)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSON, default=None)
    verification: Mapped[bool | None] = mapped_column(Boolean, default=None)
    created_at: Mapped[datetime | None]=mapped_column(DateTime,default=datetime.now,server_default=func.now())


class IngestJob(Base):
    __tablename__ = 'ingest_jobs'
    id:Mapped[uuid.UUID]=mapped_column(sqlalchemy.UUID, primary_key=True, default=uuid.uuid4)
    status:Mapped[str]=mapped_column(Text,nullable=False,default='running',server_default='running')
    total:Mapped[int]=mapped_column(Integer,nullable=False,default=0,server_default='0')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=datetime.now,server_default=func.now())
    finished_at:Mapped[datetime | None]=mapped_column(DateTime(timezone=True))

class IngestJobItem(Base):
    __tablename__ = 'ingest_job_items'

    # 领活那条 SQL 按 status 过滤再 ORDER BY，产线上这两个索引是这个查询的支撑，
    # 少了它们 claim 会退化成全表扫描（见 db/ingest_job_repository.py 的 _CLAIM_SQL）。
    __table_args__ = (
        Index('ingest_job_items_job_id_status_idx', 'job_id', 'status'),
        Index('ingest_job_items_status_id_idx', 'status', 'id'),
    )

    id:Mapped[uuid.UUID]=mapped_column(sqlalchemy.UUID, primary_key=True, default=uuid.uuid4)
    job_id:Mapped[uuid.UUID]=mapped_column(sqlalchemy.UUID, ForeignKey("ingest_jobs.id"), nullable=False)
    filename:Mapped[str]=mapped_column(Text,nullable=False)
    stored_path:Mapped[str]=mapped_column(Text,nullable=False)
    content_hash:Mapped[str]=mapped_column(Text,nullable=False)
    status:Mapped[str]=mapped_column(Text,nullable=False,default='pending',server_default='pending')
    attempts:Mapped[int]=mapped_column(Integer,nullable=False,default=0,server_default='0')
    # worker_id / lease_expires_at / doc_id / chunk_count / error 都必须是可空的：
    # 状态机回写时会把它们显式置 NULL（_set_status 清租约、_BURY_EXHAUSTED_SQL 判死），
    # 写成 NOT NULL 会让每一次入库回写都撞约束失败。
    worker_id:Mapped[str | None]=mapped_column(Text)
    lease_expires_at:Mapped[datetime | None]=mapped_column(DateTime(timezone=True))
    doc_id:Mapped[str | None]=mapped_column(Text)
    chunk_count:Mapped[int | None]=mapped_column(Integer)
    error:Mapped[str | None]=mapped_column(Text)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=datetime.now,server_default=func.now())





def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=_uuid)
    department_code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=_uuid)
    employee_no: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("departments.id"), nullable=False)
    position: Mapped[str | None] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    manager_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("employees.id"))
    status: Mapped[str] = mapped_column(String, nullable=False, default="active", server_default="active")
    hire_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=_uuid)
    project_code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    manager_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("employees.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active", server_default="active")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="P2", server_default="P2")
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_date: Mapped[datetime | None] = mapped_column(DateTime)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=_uuid)
    ticket_no: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # open,in_progress,resolved,closed
    status: Mapped[str] = mapped_column(String, nullable=False, default="open", server_default="open")
    # p1,p2,p3
    priority: Mapped[str] = mapped_column(String, nullable=False, default="P2", server_default="P2")
    # Bug,Feature Request,Performance,Access,Infrastructure,Security,RAG,Other
    category: Mapped[str | None] = mapped_column(String)
    requester_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("employees.id"), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("employees.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("projects.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
