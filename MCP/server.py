import uuid
import datetime
from typing import Literal

from fastmcp import FastMCP
import requests
from sqlalchemy import select

# 导入即生效：统一日志级别与格式（含行尾关联字段）
from app.logging_config import logger
from db import get_session
from db.models import Employee, Ticket, Project

mcp=FastMCP(
    name="weather_mcp",
    instructions='天气查询服务，可实时查询天气'
)

DEFAULT_LIMIT = 10
MAX_LIMIT = 100


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _normalize_pagination(limit: int, offset: int) -> tuple[int, int]:
    # 工具参数由大模型填写，可能给到很大的值，也可能给到负数，
    # 而 Postgres 的 LIMIT/OFFSET 不接受负数，所以统一夹到合法范围再拼进 SQL
    if limit is None or limit <= 0:
        limit = DEFAULT_LIMIT
    elif limit > MAX_LIMIT:
        logger.warning("limit 超过上限，按上限处理 limit={} max_limit={}", limit, MAX_LIMIT)
        limit = MAX_LIMIT
    if offset is None or offset < 0:
        logger.warning("offset 不合法，按 0 处理 offset={}", offset)
        offset = 0
    return limit, offset


@mcp.tool
def get_weather(city:str):
    """
    获取一个城市的天气信息

    Args:
        city:城市名

    Returns:
        返回城市的天气信息

    """
    geocoding_url = "https://geocoding-api.open-meteo.com/v1/search"
    open_meteo_url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "name":"shanghai",
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
    }
    response = requests.get(geocoding_url, params=params)
    latitude=response.json()["results"][0]["latitude"]
    longitude=response.json()["results"][0]["longitude"]

    params = {
        "latitude":latitude,
        "longitude":longitude,
        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
    }
    response = requests.get(open_meteo_url, params=params)
    return  response.json()


@mcp.tool
async def search_employees(
        name: str | None = None,
        employee_no: str | None = None,
        email: str | None = None,
        department_id: uuid.UUID | None = None,
        position: str | None = None,
        manager_id: uuid.UUID | None = None,
        status: str | None = None,
        limit:int=10,
        offset:int=0
):
    """
    根据员工信息搜索员工。

    Args:
        name: 员工姓名，可进行模糊匹配。
        employee_no: 员工编号，例如 "E001"。
        email: 员工邮箱。
        department_id: 部门 ID。
        position: 员工职位，可进行模糊匹配。
        manager_id: 直属上级员工 ID。
        status: 员工状态，例如 "active"、"inactive"、"terminated"。
        limit: 最多返回多少条员工记录，默认 10，最大 100。
        offset: 跳过前多少条记录，用于分页，默认 0。

    Returns:
        匹配的员工列表。
    """
    async with get_session() as session:
        stmt = select(Employee)
        if name:
            stmt = stmt.where(Employee.name.ilike(f"%{name}%"))
        if employee_no:
            stmt = stmt.where(Employee.employee_no == employee_no)
        if email:
            stmt = stmt.where(Employee.email == email)
        if department_id:
            stmt = stmt.where(Employee.department_id == department_id)
        if position:
            stmt = stmt.where(Employee.position.ilike(f"%{position}%"))
        if manager_id:
            stmt = stmt.where(Employee.manager_id == manager_id)
        if status:
            stmt = stmt.where(Employee.status == status)

        limit, offset = _normalize_pagination(limit, offset)
        # 分页必须有稳定的排序，否则翻页时同一行可能出现两次或整个被跳过
        stmt = stmt.order_by(Employee.employee_no).limit(limit).offset(offset)
        result = await session.execute(stmt)
        employees = result.scalars().all()

        return [
            {
                "id": employee.id,
                "no": employee.employee_no,
                "name": employee.name,
                "department_id": employee.department_id,
                "position": employee.position,
                "email": employee.email,
                "manager_id": employee.manager_id,
                "status": employee.status,
                "hire_date": employee.hire_date,
            }
            for employee in employees
        ]

@mcp.tool
async def search_tickets(
    ticket_no: str | None = None,
    title: str | None = None,
    status: Literal['open','in_progress','resolved','closed']|None=None,
    priority: Literal['P1', 'P2', 'P3']|None=None,
    category: Literal['Bug','Feature', 'Request','Performance','Access','Infrastructure','Security','RAG','Other']|None=None,
    requester_id: uuid.UUID|None=None,
    assignee_id: uuid.UUID|None=None,
    project_id: uuid.UUID|None = None,
    date_from: datetime.datetime | None = None,
    date_to: datetime.datetime | None = None,
    limit: int = 10,
    offset: int = 0,
):
    """
    Search tickets using optional filters.

    All filters are combined with AND. If no filters are provided,
    the most recently created tickets are returned first.

    Args:
        ticket_no: Ticket number for exact matching.
        title: Ticket title for exact matching.
        status: One of "open", "in_progress", "resolved", or "closed".
        priority: One of "P1", "P2", or "P3".
        category: Ticket category.
        requester_id: UUID of the employee who submitted the ticket.
        assignee_id: UUID of the assigned employee.
        project_id: UUID of the associated project.
        date_from: Start of creation date range, inclusive.
        date_to: End of creation date range, inclusive.
        limit: Maximum number of tickets to return, defaults to 10, capped at 100.
        offset: Number of tickets to skip, for paging, defaults to 0.

    Returns:
        A list of matching tickets.
    """
    async with get_session() as session:
        stmt = select(Ticket)
        if ticket_no:
            stmt = stmt.where(Ticket.ticket_no == ticket_no)
        if title:
            stmt = stmt.where(Ticket.title == title)
        if requester_id:
            stmt = stmt.where(Ticket.requester_id == requester_id)
        if assignee_id:
            stmt = stmt.where(Ticket.assignee_id == assignee_id)
        if status:
            stmt = stmt.where(Ticket.status == status.lower())
        if priority:
            stmt = stmt.where(Ticket.priority == priority.upper())
        if category:
            stmt = stmt.where(Ticket.category == category)
        if project_id:
            stmt = stmt.where(Ticket.project_id == project_id)
        if date_from:
            stmt = stmt.where(Ticket.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Ticket.created_at <= date_to)

        limit, offset = _normalize_pagination(limit, offset)
        stmt = stmt.order_by(Ticket.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        tickets = result.scalars().all()
        return {
            "tickets": [
                {
                    "id": ticket.id,
                    "ticket_no": ticket.ticket_no,
                    "title": ticket.title,
                    "description": ticket.description,
                    "status": ticket.status,
                    "priority": ticket.priority,
                    "category": ticket.category,
                    "requester_id": ticket.requester_id,
                    "assignee_id": ticket.assignee_id,
                    "project_id": ticket.project_id,
                    "created_at": ticket.created_at,
                    "updated_at": ticket.updated_at,
                    "resolved_at": ticket.resolved_at,
                    "closed_at": ticket.closed_at,
                }
                for ticket in tickets
            ],
            "count": len(tickets),
        }




@mcp.tool
async def create_ticket(
        ticket_no: str,
        title: str,
        description: str,
        requester_id: uuid.UUID,
        assignee_id: uuid.UUID,
        priority: str = 'P2',
        category: str = 'Other',
        project_id: uuid.UUID | None = None,
):
    """
    创建一个新的工单（Ticket）。

    Args:
        ticket_no: 工单编号，必须唯一，例如 "TKT-001"。
        title: 工单标题，用简短的文字描述问题或需求。
        description: 工单的详细描述，包括问题现象、背景或具体需求。
        requester_id: 提交该工单的员工 ID，必须是有效的 UUID。
        assignee_id: 负责处理该工单的员工 ID，必须是有效的 UUID。
        priority: 工单优先级，可选值为 "P1"、"P2"、"P3"，默认为 "P2"。
        category: 工单分类，例如 "Bug"、"Feature Request"、"Performance"、
            "Access"、"Infrastructure"、"Security"、"RAG"、"Other"，
            默认为 "Other"。
        project_id: 工单所属项目的 ID，必须是有效的 UUID；如果工单不属于任何项目，
            可以不提供，默认为 None。

    Returns:
        创建成功后的工单信息。
    """
    ticket=Ticket(
        ticket_no=ticket_no,
        title=title,
        description=description,
        requester_id=requester_id,
        assignee_id=assignee_id,
        priority=priority,
        category=category,
        project_id=project_id,
    )
    async with get_session() as session:
        session.add(ticket)
    return '工单创建成功'


@mcp.tool
async def search_project(
        id:uuid.UUID|None=None,
        project_code:str|None=None,
        status:str|None=None,
        priority:str|None=None,
        manager_id:uuid.UUID|None=None,
        date_from:datetime.datetime|None=None,
        date_to:datetime.datetime|None=None,
        limit:int=10,
        offset:int=0,
):
    """
Search projects using optional filters.

All provided filters are combined with AND.
If no filters are provided, all projects are returned,
ordered by start date in descending order.

Args:
    id: UUID of the project.
    project_code: Project code for exact matching, such as "PROJ-001".
    status: Project status, such as "active" or "completed".
    priority: Project priority, such as "P1", "P2", or "P3".
    manager_id: UUID of the employee who manages the project.
    date_from: Start date of the project date range, inclusive.
    date_to: End date of the project date range, inclusive.
    limit: Maximum number of projects to return, defaults to 10, capped at 100.
    offset: Number of projects to skip, for paging, defaults to 0.

Returns:
    A list of matching project information.
    """
    async with get_session() as session:
        stmt=select(Project)
        if id:
            stmt=stmt.where(Project.id == id)
        if project_code:
            stmt=stmt.where(Project.project_code == project_code)
        if status:
            stmt=stmt.where(Project.status == status.lower())
        if manager_id:
            stmt=stmt.where(Project.manager_id==manager_id)
        if priority:
            stmt=stmt.where(Project.priority == priority.upper())
        if date_from:
            stmt=stmt.where(Project.start_date >= date_from)
        if date_to:
            stmt=stmt.where(Project.start_date <= date_to)

        limit, offset = _normalize_pagination(limit, offset)
        stmt = stmt.order_by(Project.start_date.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        projects = result.scalars().all()
        return [
            {
            "id":project.id,
            "project_code":project.project_code,
            "name":project.name,
            "description":project.description,
            "manager_id":project.manager_id,
            "status":project.status,
            "priority":project.priority,
            "start_date":project.start_date,
            "end_date":project.end_date
        }
            for project in projects
        ]





if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="127.0.0.1",
        port=8001,
    )

