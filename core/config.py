from dotenv import load_dotenv
import os
load_dotenv(override=True)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL")

POSTGRESQL_URL = os.getenv("POSTGRESQL_URL")

MILVUS_URI=os.getenv("MILVUS_URI")
MILVUS_DB=os.getenv("MILVUS_DB")
MILVUS_COLLECTION=os.getenv("MILVUS_COLLECTION")
EMBEDDING_MODEL=os.getenv("EMBEDDING_MODEL")

# rerank 模型路径。本地权重目录和 HuggingFace 模型 id 两种都行——FlagReranker 都收，
# 给 id 时首次会去下载。本机的权重目录写在 .env 里覆盖，不要把绝对路径写回代码，
# 那是别人机器上不存在的路径。
RERANKER_MODEL_PATH=os.getenv("RERANKER_MODEL_PATH", "BAAI/bge-reranker-v2-m3")

TAVILY_API_KEY=os.getenv("TAVILY_API_KEY")
VISION_CONCURRENCY = int(os.getenv("VISION_CONCURRENCY", "6"))

# --- 路径 ---

# 上传文件的落盘根目录。按模块位置推导，不受启动时工作目录影响
# （worker 进程的 CWD 不一定在仓库根）。API 负责往里写，worker 负责用完清理。
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_ROOT = os.path.join(_BASE_DIR, "uploads")

# --- ingest worker（app/worker.py 与 db/ingest_job_repository.py）---

# 领到任务后多久算过期。解析大文件可能远超这个时长，worker 会在到期前续租；
# 进程崩掉没续上，这行就会被别的 worker 重新领走。
INGEST_LEASE_MINUTES = int(os.getenv("INGEST_LEASE_MINUTES", "10"))
# 续租间隔。必须明显小于租约时长，否则还没等续上就被回收了。
INGEST_HEARTBEAT_SECONDS = int(os.getenv("INGEST_HEARTBEAT_SECONDS", "120"))
# 单个文件最多被领几次，超过就判死。
INGEST_MAX_ATTEMPTS = int(os.getenv("INGEST_MAX_ATTEMPTS", "3"))
# 队列空时的轮询间隔。
INGEST_POLL_SECONDS = int(os.getenv("INGEST_POLL_SECONDS", "5"))
# run_workers.py 默认起几个 worker 进程。调大的真正上限是出网 API 并发
# （进程数 × 在飞文档数 × VISION_CONCURRENCY），不是内存。
INGEST_WORKERS = int(os.getenv("INGEST_WORKERS", "2"))

MCP_CALL_TIMEOUT_SECONDS=5
# MCP server 的 streamable-http 端点。注意三点都是必需的：scheme 是 http
# （server.py 里 mcp.run 没配 TLS）、要带 /mcp 路径。
MCP_SERVER_URL='http://127.0.0.1:8001/mcp'
MCP_INIT_TIMEOUT_SECONDS=5

# 单个工具的退避重试次数。总等待 ≈ 超时×次数 + 退避，别调太大。
RETRY_MAX_ATTEMPTS=4
RETRY_BASE_DELAY=0.5
RETRY_MAX_DELAY=8

# 大模型输出格式错误时的回灌修复轮数。设 0 即退回"不修复"的原行为。
MAX_REPAIR_ROUNDS=2
MAX_DECISION_ATTEMPTS=2

# 工具链路总时长上限。修复轮数和退避重试会叠乘
# （最多 (1+MAX_REPAIR_ROUNDS) × RETRY_MAX_ATTEMPTS 次调用），
# 没有这个兜底，最坏情况一次请求能拖到分钟级。
TOOL_TOTAL_BUDGET_SECONDS=45


# 大模型调用异常的退避时间
LLM_RETRY_BASE_DELAY=1
LLM_RETRY_MAX_DELAY=10

# --- 启动时自动拉起后台服务（ingest workers / MCP server）---
# 置 "0" 关闭。开发时若用 uvicorn --reload，每次热重载都会重新拉起 worker
# （每个常驻 1.5–3GB Docling 模型），嫌重就关掉单独手动起。
AUTO_START_WORKERS = os.getenv("AUTO_START_WORKERS", "1") == "1"
AUTO_START_MCP = os.getenv("AUTO_START_MCP", "1") == "1"
