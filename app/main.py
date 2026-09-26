
import asyncio
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from copy import deepcopy

from fastapi import FastAPI, File, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse

# 导入即生效：统一日志级别与格式（含行尾的 thread_id/job_id 等关联字段）。
# 必须早于其它本地模块导入，否则它们 import 阶段打的日志会走默认格式。
from app.logging_config import logger
from app.api import routes_ingest, routes_chat, routes_thread, routes_document
from fastapi.middleware.cors import CORSMiddleware


# 仓库根目录（app/ 的上一级）。用于 import 根目录的 run_workers，以及用 -m 方式
# 启动 MCP.server（其顶层是 from db import ...，需要仓库根在 sys.path 上）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _start_worker_supervisor():
    """在后台线程里跑 run_workers 的监督循环，退出时通过 stop_event 收尾。

    不把 run_workers.py 当子进程 spawn，而是进程内起线程——Windows 下 terminate()
    是硬杀，直接杀 run_workers.py 会把它拉起的 worker 子进程变孤儿；在进程内跑，
    stop_event.set() 会走 run_workers._shutdown 正常终止 worker。
    """
    import run_workers

    stop = threading.Event()
    t = threading.Thread(
        target=run_workers.main,
        args=([],),               # 无参数 → 用 INGEST_WORKERS（默认 2）
        kwargs={"stop_event": stop},
        daemon=True,
        name="ingest-worker-supervisor",
    )
    t.start()
    return stop, t


def _start_mcp_server():
    """以模块方式启动 MCP server，cwd 指向仓库根，保证 from db 能解析。"""
    return subprocess.Popen([sys.executable, "-m", "MCP.server"], cwd=_REPO_ROOT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core.config import AUTO_START_WORKERS, AUTO_START_MCP

    worker_stop = worker_thread = None
    mcp_proc = None

    if AUTO_START_WORKERS:
        worker_stop, worker_thread = _start_worker_supervisor()
        logger.info("ingest worker 监督器已启动，进程数由 INGEST_WORKERS 决定")
    if AUTO_START_MCP:
        mcp_proc = _start_mcp_server()
        logger.info("MCP server 已启动 pid={}", mcp_proc.pid)

    # rerank 模型在后台预热：载入要读权重、上显存，提前在启动时完成，
    # 避免第一个走检索分支的请求卡顿。即便这里失败，get_reranker 的
    # lru_cache 也会在首次实际使用时兜底重载（会再阻塞一次）。
    from core.reranker import get_reranker

    async def _preload_reranker():
        try:
            await asyncio.to_thread(get_reranker)
            logger.info("rerank 模型后台载入完成")
        except Exception as e:
            logger.warning("rerank 模型后台载入失败，将在首次使用时重试 error={}", e)

    asyncio.create_task(_preload_reranker())

    # BM25 索引同样后台预热。它要全表扫一遍 Milvus 再把每个 chunk 过 jieba 分词建
    # 索引，冷启动后的第一个检索请求会把这些代价整个吃在请求路径上（而且并发到达
    # 的其它检索请求会在 _index_lock 上排队等它建完）。这里提前做掉，第一问只需
    # 等待指纹比对那一条很小的查询。同样不阻塞启动，失败就让首问按原路径自建。
    from retrieval.bm25_retrieval import get_bm25

    async def _preload_bm25():
        try:
            index = await get_bm25()
            logger.info("bm25 索引后台构建完成 chunks={}", len(index.ids))
        except Exception as e:
            logger.warning("bm25 索引后台构建失败，将在首次检索时重试 error={}", e)

    asyncio.create_task(_preload_bm25())

    # 启动时探一次 MCP：URL/认证这类配置错误要在这里暴露清楚（记 ERROR），
    # 而不是等第一个用户提问才在请求路径上反复猜。server 还没起是正常情况
    # （开发时经常先起 API 后起 MCP），只记 WARNING，不阻塞启动。
    from MCP.client import get_mcp_client
    from core.retry import is_fatal_endpoint_error

    try:
        await get_mcp_client().list_tools_with_backoff(max_attempts=1)
        logger.info("MCP 工具目录加载成功")
    except Exception as e:
        if is_fatal_endpoint_error(e):
            logger.error("MCP 端点配置有误，工具分支将不可用 error={}", e)
        else:
            logger.warning("MCP 服务当前不可达（可能尚未启动），工具分支将按需重试 error={}", e)

    try:
        yield
    finally:
        # 收尾顺序：先停 worker（stop_event 触发 run_workers._shutdown 终止 worker 子进程），
        # 再停 MCP server。join 给足 _shutdown 的宽限 + 终止等待时间。
        if worker_stop is not None:
            worker_stop.set()
            worker_thread.join(timeout=25)
        if mcp_proc is not None and mcp_proc.poll() is None:
            mcp_proc.terminate()
            try:
                mcp_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mcp_proc.kill()


app = FastAPI(docs_url=None, lifespan=lifespan)


# =========================
# 实际 API
# =========================


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_ingest.router)
app.include_router(routes_chat.router)
app.include_router(routes_document.router)
app.include_router(routes_thread.router)



# =========================
# 给 Swagger UI 使用的 OpenAPI
# =========================



@app.get("/openapi-swagger.json", include_in_schema=False)
async def swagger_openapi():
    schema = deepcopy(app.openapi())

    # 使用 OpenAPI 3.0
    schema["openapi"] = "3.0.3"

    # 找到 /ingest
    upload_schema = (
        schema["paths"]["/ingest"]["post"]
        ["requestBody"]["content"]["multipart/form-data"]
        ["schema"]
    )

    # 找到实际的 Body schema
    body_name = upload_schema["$ref"].split("/")[-1]
    body_schema = schema["components"]["schemas"][body_name]

    # 强制告诉 Swagger UI：
    # files 是一个文件数组
    body_schema["properties"]["files"] = {
        "type": "array",
        "title": "Files",
        "items": {
            "type": "string",
            "format": "binary"
        }
    }

    return JSONResponse(schema)


# =========================
# 自定义 Swagger UI
# =========================

@app.get("/docs", include_in_schema=False)
async def custom_docs():
    return get_swagger_ui_html(
        openapi_url="/openapi-swagger.json",
        title="API Docs",
        swagger_js_url=(
            "https://unpkg.com/swagger-ui-dist@5/"
            "swagger-ui-bundle.js"
        ),
        swagger_css_url=(
            "https://unpkg.com/swagger-ui-dist@5/"
            "swagger-ui.css"
        ),
    )