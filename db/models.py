import uuid

import sqlalchemy
from sqlalchemy import String, DateTime, ForeignKey, JSON, Text, func, Boolean, Integer, UUID
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
    title: Mapped[str] = mapped_column(String, default="New chat")
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.now)
    updated_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.now)

class Document(Base):
    __tablename__ = "documents"
    doc_id:Mapped[str]=mapped_column(String, primary_key=True, default=_uuid)
    filename:Mapped[str]=mapped_column(String)
    file_path:Mapped[str]=mapped_column(String)
    file_type:Mapped[str]=mapped_column(String)
    upload_time:Mapped[datetime]=mapped_column(DateTime,default=datetime.now)

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
    sources: Mapped[list] = mapped_column(JSON, default=None)
    verification: Mapped[bool] = mapped_column(Boolean, default=None)
    created_at: Mapped[datetime]=mapped_column(DateTime,default=datetime.now)


class IngestJob(Base):
    __tablename__ = 'ingest_jobs'
    id:Mapped[uuid.UUID]=mapped_column(sqlalchemy.UUID, primary_key=True, default=uuid.uuid4)
    status:Mapped[str]=mapped_column(String,nullable=False,default='running')
    total:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=datetime.now)
    finished_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))

class IngestJobItem(Base):
    __tablename__ = 'ingest_job_items'
    id:Mapped[uuid.UUID]=mapped_column(sqlalchemy.UUID, primary_key=True, default=uuid.uuid4)
    job_id:Mapped[uuid.UUID]=mapped_column(sqlalchemy.UUID, ForeignKey("ingest_jobs.id"), nullable=False)
    filename:Mapped[str]=mapped_column(String,nullable=False)
    stored_path:Mapped[str]=mapped_column(String,nullable=False)
    content_hash:Mapped[str]=mapped_column(Text,nullable=False)
    status:Mapped[str]=mapped_column(String,nullable=False,default='pending')
    attempts:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    worker_id:Mapped[str]=mapped_column(String)
    lease_expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    doc_id:Mapped[str]=mapped_column(String)
    chunk_count:Mapped[int]=mapped_column(Integer)
    error:Mapped[str]=mapped_column(Text)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=datetime.now)





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
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    hire_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=_uuid)
    project_code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    manager_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("employees.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="P2")
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_date: Mapped[datetime | None] = mapped_column(DateTime)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=_uuid)
    ticket_no: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # open,in_progress,resolved,closed
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    # p1,p2,p3
    priority: Mapped[str] = mapped_column(String, nullable=False, default="P2")
    # Bug,Feature Request,Performance,Access,Infrastructure,Security,RAG,Other
    category: Mapped[str | None] = mapped_column(String)
    requester_id: Mapped[uuid.UUID] = mapped_column(UUID, ForeignKey("employees.id"), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("employees.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID, ForeignKey("projects.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

