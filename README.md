# Enterprise Knowledge Base QA System

一个面向企业内部文档的知识库问答系统：上传企业文档（PDF / Word / PPT / 图片），系统异步解析、切块、向量化入库；提问时由 LangGraph 判断该走**知识库检索**、**业务系统工具调用**还是**直接回答**，并把结果落成可追溯的会话。

## 这个项目解决了什么

企业内部问答案例里，难点通常不在"能不能调通大模型"，而在下面这些地方——它们也是这个仓库着力最多的部分：

- **入库不能卡住请求**。Docling 解析一份大文档要几十秒到几分钟，所以 `POST /ingest` 只登记任务就立刻返回 202，真正的解析由独立 worker 进程消费，靠数据库租约（`FOR UPDATE SKIP LOCKED` + 心跳续租）保证多进程不重领、进程崩了能被自动捡回。
- **光靠向量检索查不准**。企业文档里大量是编号、代码、专有名词，纯语义检索经常漏，所以用**向量 + BM25 双路召回再做 RRF 融合**，融合结果再用 rerank 模型逐篇打分，带主阈值 + 兜底阈值两级筛选。
- **不是所有问题都该查文档**。"我的工单处理到哪了"这类问题查知识库只会得到幻觉，所以先做查询分类，该走工具链路的走 MCP 工具查业务库。
- **大模型和工具都会不按套路出牌**。结构化输出会被 token 上限截断、参数会填错、MCP 会话会断、HTTP 会 429——这些失败各自有独立的处理策略（重试 / 回灌修复 / 上报"结果不确定"），而不是一句 `except: pass`。

## 架构

```mermaid
flowchart TB
    FE["Vue 3 前端<br/>问答页 + 文档管理页"]

    subgraph API["API 进程 (FastAPI)"]
        ING["POST /ingest<br/>流式落盘 + 建任务"]
        CHAT["POST /chat"]
        LG["LangGraph 问答图"]
        BM["BM25 索引<br/>(进程内缓存)"]
    end

    subgraph WK["ingest worker 进程 × N"]
        PR["Docling 解析<br/>+ 视觉模型识图"]
        VEC["embedding + 写向量"]
    end

    DB[("PostgreSQL<br/>文档元数据 / 会话 / 入库队列 / 业务表")]
    MV[("Milvus<br/>chunk + 向量")]
    MCP["MCP server<br/>员工 / 工单 / 项目查询"]
    LLM["DeepSeek / OpenRouter"]

    FE --> ING --> DB
    DB -. "队列" .-> WK
    PR --> VEC --> MV
    WK --> DB
    FE --> CHAT --> LG
    LG --> MV
    LG --> BM
    LG --> MCP
    LG --> LLM
    MCP --> DB
```

问答图的走向：

```mermaid
flowchart LR
    START --> classify["classify_query<br/>(是否需要检索/改写/工具)"]
    classify -->|直接回答| direct[direct_generate_answer]
    classify -->|检索| rw[rewrite_query_standalone] --> ret[enterprise_retrieve<br/>向量+BM25+RRF]
    ret --> rr["rerank<br/>阈值筛选 + 兜底"]
    rr -->|无相关文档| rw2[rewrite_query_retry] --> ret
    rr --> gen[generate_by_rag] --> verify{verify_answer}
    verify -->|不通过| rw2
    verify -->|通过| persist[persist_conversation]
    classify -->|工具| tool["tool_use<br/>MCP 决策+执行+回灌修复"]
    tool --> chk{check_tool_result}
    chk -->|不够| tool
    chk -->|够了| gen2[generate_by_tool] --> persist
    direct --> persist
```

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | FastAPI + Uvicorn，SQLAlchemy async |
| 问答编排 | LangGraph（状态图 + 条件路由） |
| 大模型 | DeepSeek（对话 / 视觉）、OpenRouter（备用） |
| 文档解析 | Docling，图片交给视觉模型 |
| 检索 | Milvus（向量）+ rank-bm25（全文）+ RRF 融合 + FlagEmbedding rerank |
| 工具调用 | FastMCP（独立进程，HTTP 传输） |
| 前端 | Vue 3 + Element Plus + Pinia + Vite |
| 日志 | loguru |
| 评估 | ragas |

## 快速开始

### 1. 前置

- Python 3.12
- PostgreSQL（需要业务表：`departments` / `employees` / `projects` / `tickets`，以及文档与会话表）
- Milvus（standalone 即可）
- 一个 DeepSeek API key（对话 + 视觉 + embedding 都走它）

仓库里没有建表脚本，默认这些表和 collection 已经存在。

### 2. 安装

```bash
pip install -r requirements.txt
```

需要 GPU 时改用 PyTorch 官方源装对应 CUDA 版本：

```bash
pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cu126
```

### 3. 配置

```bash
cp .env.example .env
```

`.env.example` 里每个变量都有注释说明是否必需。最少要填 `DEEPSEEK_API_KEY`、`POSTGRESQL_URL`、`MILVUS_URI`、`EMBEDDING_MODEL`。

### 4. 启动

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动 API 时会**自动拉起** ingest worker 监督器和 MCP server（由 `AUTO_START_WORKERS` / `AUTO_START_MCP` 控制，默认开启），并用后台任务预热 rerank 模型和 BM25 索引，所以正常情况下一条命令就够了。

如果你想手动分别启动（例如用 `--reload` 开发时，热重载会反复拉起每个常驻 1.5–3GB 的 worker）：

```bash
python run_workers.py 3       # 起 3 个 ingest worker，进程管理器会自动重启挂掉的
```

```bash
python -m MCP.server          # 必须用 -m 模块方式，脚本方式会因 from db 解析失败
```

### 5. 前端

```bash
cd frontend && npm install && npm run dev
```

默认 `http://localhost:5173`（与后端 CORS 白名单一致）。后端地址在 `frontend/src/api/client.js` 配置。

访问 `http://127.0.0.1:8000/docs` 可以看到后端接口文档。

## 设计要点

几个值得单独说明的实现决定，完整版在 [`docs/agent.md`](docs/agent.md)：

**入库是生产者/消费者结构。** 请求侧和 worker 侧不直接通信，只通过 `ingest_job_items` 表交接。领活用一条 SQL 同时完成"挑待处理的 / 挑租约过期的僵尸 / 原子置为 running 并挂租约"，不需要额外的调度器或 reaper。并发度只来自进程数，分发由数据库的 `FOR UPDATE SKIP LOCKED` 完成。文件内容哈希决定 `doc_id`，所以同一份文件重复上传或重试会落到同一批主键上，Milvus 的 upsert 才是真的覆盖而不是灌重复数据。

**BM25 索引靠语料指纹失效。** 索引建在 API 进程内存里，而入库发生在 worker 进程，两边没有通知通道。所以每次检索前读一次 `documents` 表的 `(行数, 最大上传时间)` 作为指纹，和自己建索引时记下的比对，不一致就重建。指纹必须在开始扫描 Milvus **之前**读取，否则扫描期间新写入的文档会被记进指纹却不在索引里，那份索引就永远漏掉它。

**工具链路带失败回灌。** 参数填错、服务端瞬时故障、结果不确定、不可重试——四类失败分别处理。值得重试的失败会把**该工具的正确参数定义（input_schema）原样回灌给模型**重新决策，实测比只丢一句"参数错了"有效得多；非幂等的写操作（`create_ticket`）在"请求可能已送达"时不会重发，而是如实上报不确定性。

**大模型调用有独立的容错层。** 异常按类型分类成可重试 / 不可重试，指数退避带抖动；输出被 token 上限截断时扩容重试，结构化输出不合规时把校验错误回灌成下一轮输入。异常分类是纯函数、不打日志，日志只记在真正的边界上——这样同一个异常不会在三个层次各打一遍，也不会把"会被成功修复的错误"记成 ERROR。

**日志带关联标识。** 并发跑多个 worker、多个会话时，靠日志本身分不清哪几行属于同一次请求。现在入口处用 `logger.contextualize` 绑一次（`/chat` 绑 `thread_id`，worker 绑 `worker_id`+`item_id`，`/ingest` 绑 `job_id`），靠 contextvars 自动穿透 LangGraph 节点、检索层和 MCP 工具链路，行尾统一渲染成 `| thread_id=...`。级别由 `LOG_LEVEL` 控制，调试时设成 `DEBUG` 能看到检索耗时和逐篇 rerank 打分。

## 目录结构

```text
app/          FastAPI 入口、API router、schema、日志配置、ingest worker
core/         配置、LLM 调用与容错、reranker、embedding、提示词
db/           SQLAlchemy 模型、PostgreSQL/Milvus 仓储层、入库任务状态机
ingestion/    文档解析、图片理解、切块
retrieval/    向量检索、BM25、RRF 混合检索
graph/        LangGraph 状态、节点、路由、编译入口
MCP/          MCP 工具服务端（独立进程）与工具路由客户端
frontend/     Vue 3 前端
eval/         离线评估脚本与各轮评估结果
docs/         项目文档
run_workers.py  ingest worker 的进程管理器
```

## 评估

`eval/` 下有多轮离线评估（`1st_eval` … `6th_eval`），用 ragas 的 Context Precision / Recall / Faithfulness / Answer Relevancy 四项指标，记录了 rerank 替换、chunk 策略调整等改动前后的对比。

各轮目录里的 `rag_result.jsonl` / `eval_result.jsonl` 保存了评估时**检索到的文档片段原文和参考答案**，体积不小；它们是为了让指标可复核才保留的，不是示例数据。

## 已知限制

- **没有测试套件**。改动后最基础的验证是 `python -m compileall`，以及针对被改模块做导入/单点调用。
- **`db/models.py` 没有迁移**，表结构靠外部已存在的库与之对齐。
- **每个 ingest worker 进程常驻一份 Docling 模型**（约 1.5–3GB），并行度的真实上限是内存而不是 CPU。
- **BM25 索引每次语料变化都要整体重建**，重建落在下一个检索请求的路径上（启动时已预热一次，但入库之后那次重建仍会让该请求变慢）。
- **`verify_answer` 只把模型输出精确等于 `YES` 视为通过**，这是脆弱契约：换模型后如果返回 `Yes.` 之类，会把答案推去重写白跑一轮。
