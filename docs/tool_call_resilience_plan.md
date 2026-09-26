# MCP 工具调用容错方案

目标：给工具分支（`classify_query → tool_path → tool_use → check_tool_result → ...`）加上失败处理。

- **临时性抖动**（网络波动、接口超时）→ 指数退避重试。
- **大模型返回格式错误**（参数类型不符、缺参数、JSON 语法错误）→ 把报错回灌给大模型，让它重新决策。

---

## 0. 结论速览

| 失败类型 | 出现在哪 | 处理方式 | 重试次数 |
|---|---|---|---|
| 传输层抖动（连接断开、读超时、服务重启） | `list_tools` / `call_tool` 的 HTTP 会话 | 重建会话 + 指数退避 + 抖动 | 默认 4 次 |
| 决策阶段结构化输出失败（JSON 语法、schema 不符） | `make_toolcall_decision` | 带错误信息重新 invoke 模型 | 默认 2 次 |
| 工具参数语义错误（参数名/类型/必填项） | `call_tool` 返回 `ToolError` | **不重试**，把错误 + 该工具 schema 回灌给模型重新决策 | 默认 2 轮 |
| 查询为空 | `call_tool` 正常返回、`content` 为空 | **不是失败**，绝不重试 | 0 |
| 其他（认证失败、代码 bug） | 任意 | 快速失败 + 记日志 | 0 |

前提：**必须先补上超时**。现在全链路没有超时，没有"失败"可言，只会一直挂住（见 Step 1）。

---

## 1. 两类失败落在代码里的两个不同位置

这是本方案的核心判断。你说的两类失败，在代码里根本不在同一层：

### 第一类：传输抖动 —— 在 `MCP/client.py` 的 HTTP 会话层

- `client.py:92-103` `get_catalog_text()` 里的 `client.list_tools()`
- `client.py:111-122` `tool_call()` 里的 `client.call_tool(...)`

MCP server 跑在 `127.0.0.1:8001`，但它的后端是 PostgreSQL，进程也可能正在重启，所以本地回环照样会抖。另外真正远端的抖动主要来自 **DeepSeek / OpenRouter 的模型调用**，那也是要退避的。

这一层是**进程内自动重试**，用户看不见，也不需要模型参与。

### 第二类：格式/参数错误 —— 在两个不同的子层

**子层 A：决策阶段。** `client.py:124-136` `make_toolcall_decision()` 用 `with_structured_output(ToolCallDecision)`。模型吐出坏 JSON、或者 `arguments` 类型对不上时，这里抛异常。**现在的处理是 `client.py:135-136` 只记日志不 re-raise，`self.toolcall_decision` 保持 `None`**，紧接着 `client.py:118` 遍历 `self.toolcall_decision.tool_calls` 直接 `AttributeError`。这是现状里的一个确定性崩溃点。

**子层 B：执行阶段。** 决策本身是合法 JSON，但参数对工具来说是错的 —— 参数名拼错、类型不符、少传必填项、工具名不存在。FastMCP 4.0.4 在调用前按 `input_schema` 校验参数，不合格会抛 `ToolError`（`ToolError → FastMCPError → Exception`）。`MCP/server.py` 里的 5 个工具自己都不 `raise`，所以这一层错误**全部**来自校验，是纯粹的确定性错误：**原样重发一百次结果完全一样**。

所以子层 B 必须走"改变输入"的路径，也就是你说的回灌给大模型。这也是本方案里最值得做的部分。

### 顺带一个已经存在但没用上的通道

`graph/node.py:363`：

```python
question = state.get('question','') + '\n' + state.get('tool_result','')
```

**上一轮的 `tool_result` 已经被拼进下一轮决策的 question 里了**，而 `routes.py:41-51` 也会把图循环回 `tool_use`。也就是说"把工具结果回灌给模型"的通道天然存在。

但**不要依赖它做参数修复**，原因有三：

1. 它要求 `check_tool_result` 正确判断出 `need_more_tools=True`，而那个判断本身又是一次 LLM 调用，不可靠；
2. 工具调用一旦抛异常，节点就中断了，根本走不到 `check_tool_result`；
3. 走一圈图要重新跑 `check_tool_result` + `generate_by_tool`，代价远高于在节点内部重来一轮。

**结论：修复循环做在节点内部（内层），图循环保持不变只用于"信息还不够，再查一次"（外层）。**

---

## 2. 设计

分三层，职责不重叠：

```
┌─ 模型层 ────────────────────────────────────────────┐
│ generate_by_tool      ← 拿最终 tool_result 生成回答  │  (不动)
└──────────────────────────────────────────────────────┘
┌─ 修复循环（新增，MCP/tool_runner.py）────────────────┐
│ 决策 → 执行 → 有 repairable 失败？→ 带错误+schema   │
│ 回灌模型 → 重新决策（最多 MAX_REPAIR_ROUNDS 轮）     │
└──────────────────────────────────────────────────────┘
┌─ 退避层（新增，core/retry.py）───────────────────────┐
│ 只处理 transient：重建会话 + 指数退避 + 抖动          │
└──────────────────────────────────────────────────────┘
┌─ 传输层 ─────────────────────────────────────────────┐
│ MCPClient：建连接 / list_tools / call_tool           │  (加超时)
└──────────────────────────────────────────────────────┘
```

**只有 transient 在退避层循环，repairable 和 fatal 立刻往上传。** 两者绝不混在一个循环里 —— 混在一起就会出现"参数写错了在那儿退避重发 4 次"这种纯浪费时间的行为。

---

## 3. 分步实施

### Step 1 —— 先补超时（前提，必须先做）

现在 `client.py:81-91` 建 `Client` 时只传了 transport，**没有任何超时**。没有超时，一次网络挂起就会把请求永久卡住，退避逻辑永远等不到异常。

确认过的 API 事实（fastmcp 4.0.4 / mcp 2.2.0）：

- `StreamableHttpTransport.__init__` **没有** timeout 参数（只有 `url / headers / auth / httpx_client_factory / verify`），所以别想着在 transport 上设。
- `Client.__init__` **有** `timeout` 和 `init_timeout`。
- `Client.call_tool(name, arguments, *, timeout=..., raise_on_error=...)` 单次调用也能设超时。

改 `MCP/client.py`：

```python
def get_client(self) -> Client:
    sys.stdout.reconfigure(encoding='utf-8', errors='replaces')
    TRANSPORT = StreamableHttpTransport(url=MCP_SERVER_URL)
    return Client(
        transport=TRANSPORT,
        timeout=MCP_CALL_TIMEOUT_SECONDS,      # 单次工具调用
        init_timeout=MCP_INIT_TIMEOUT_SECONDS, # 握手
    )
```

超时值的选取要和退避预算一起算：`超时 × 最大尝试次数 + 退避总时长` 必须明显小于 `routes_chat.py` 的请求超时，否则重试还没跑完，客户端已经断了。

### Step 2 —— 新增 `core/retry.py`：分类器 + 退避

`tenacity 8.5.0` 已经在环境里（langchain-core 的传递依赖），不用新增依赖。但**分类逻辑是本项目特有的**，所以手写一个薄封装，只在需要时用 tenacity 的 `AsyncRetrying` 做循环。

```python
import random
from typing import Literal
import anyio, httpx
from fastmcp.exceptions import ToolError
from loguru import logger

Kind = Literal["transient", "repairable", "fatal"]

# httpx.TransportError 是伞类，覆盖 ConnectError / ReadTimeout /
# ConnectTimeout / RemoteProtocolError / ReadError / PoolTimeout。
# 服务重启时 anyio 会抛 ClosedResourceError / BrokenResourceError。
# 3.12 里 asyncio.TimeoutError 就是内置 TimeoutError。
_TRANSIENT = (
    httpx.TransportError,
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    TimeoutError,
)

def classify(exc: BaseException, *, where: str) -> Kind:
    """where 是调用点，决定默认语义；异常类型只做覆盖。

    ★ 关键：同一个异常类在不同调用点含义不同。
      ToolError 出现在 call_tool 是"参数写错了"（repairable），
      出现在建连阶段则是"服务端有问题"（transient）。
      所以不能只按类型分，必须先看从哪一步抛出来的。
    """
    if isinstance(exc, _TRANSIENT):
        return "transient"
    if where == "call_tool" and isinstance(exc, ToolError):
        return "repairable"
    if where == "llm_structured":
        # with_structured_output 失败：JSON 语法错、schema 校验不过
        from pydantic import ValidationError
        from langchain_core.exceptions import OutputParserException
        if isinstance(exc, (ValidationError, OutputParserException, ValueError)):
            return "repairable"
    return "fatal"

def backoff_delay(attempt: int) -> float:
    """attempt 从 1 开始。指数 + 抖动。

    抖动是必须的：多个工具并发失败时，不加抖动会整齐划一地重发，
    把刚恢复的服务再打挂一次。
    """
    raw = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
    return raw * (0.5 + random.random() * 0.5)
```

**一个容易做错的地方**：重试的粒度不能是"单独重发 `call_tool`"。如果底层的 MCP 会话已经坏了，在同一个坏会话上重发只会连续失败。**重试单位必须是"重建会话 → 调工具"整个块**：

```python
async def call_with_backoff(mcp, tool_name, arguments, *, max_attempts):
    for attempt in range(1, max_attempts + 1):
        try:
            # 每次尝试都走一遍完整的 async with —— 会话是新的
            return await mcp.call_one(tool_name, arguments)
        except BaseException as exc:
            kind = classify(exc, where="call_tool")
            if kind != "transient" or attempt == max_attempts:
                raise ToolFailure(kind=kind, tool_name=tool_name,
                                  arguments=arguments, cause=exc) from exc
            delay = backoff_delay(attempt)
            logger.warning(
                f"工具 {tool_name} 第 {attempt} 次调用遇到瞬时故障，"
                f"{delay:.2f}s 后重试：{exc!r}"
            )
            await asyncio.sleep(delay)
```

### Step 3 —— 改造 `MCP/client.py`

三处具体改动：

**3.1 `get_catalog_text()` 加退避 + 保留结构化 schema**

现在 `client.py:97-102` 把工具目录拼成一个大字符串就丢掉了结构。修复循环回灌错误时**必须带上该工具的正确参数 schema**（只给一句"参数错了"模型改不对），所以要把结构化结果留下：

```python
async def get_catalog_text(self):
    if self.catalog_text is not None:
        return self.catalog_text
    tools = await list_tools_with_backoff(...)   # 同样按 transient 退避
    self.tools_by_name = {t.name: t for t in tools}   # ← 新增，供回灌时取 schema
    self.catalog_text = "\n".join(
        f"- {t.name}: {t.description}\n  参数: {t.input_schema}"
        for t in tools
    )
    return self.catalog_text
```

**3.2 `tool_call()` 改造成逐工具隔离**

现在 `client.py:112-122` 是 `asyncio.gather(*tasks)` 且**没有 `return_exceptions=True`** —— 一个工具失败整批全挂。改成：

```python
results = await asyncio.gather(*tasks, return_exceptions=True)
```

把每个结果（正常结果或异常）配对成 `ToolOutcome`，逐个分类，成功的保留、失败的分流。

**3.3 `make_toolcall_decision()` 不再吞异常**

`client.py:135-136` 现在 `except` 后什么都不做，留下 `toolcall_decision=None` 让下游炸。改成把异常往上抛（由修复循环处理），并且**决策失败本身也要有修复路径**：

```python
async def make_toolcall_decision(self, history, question, error_feedback=""):
    for attempt in range(1, MAX_DECISION_ATTEMPTS + 1):
        try:
            return await llm_with_structured.ainvoke(messages)
        except Exception as e:
            kind = classify(e, where="llm_structured")
            if kind != "repairable" or attempt == MAX_DECISION_ATTEMPTS:
                raise
            # 把校验错误本身变成下一轮的输入
            messages = messages + [
                AIMessage(content=repr(getattr(e, "raw_output", ""))),
                HumanMessage(content=f"上次输出无法解析，错误如下，请重新输出：\n{e}"),
            ]
```

### Step 4 —— 新增 `MCP/tool_runner.py`：修复循环

这是"参数错误回灌大模型"的落点。

```python
async def run_tools(question: str, history: str) -> str:
    mcp = get_mcp_client()
    catalog = await mcp.get_catalog_text()

    feedback = ""        # 上一轮的错误，作为下一轮决策的输入
    outcomes = []

    for round_no in range(1, MAX_REPAIR_ROUNDS + 2):
        decision = await mcp.make_toolcall_decision(history, question, feedback)
        outcomes = await execute_all(mcp, decision)

        failed = [o for o in outcomes if o.kind in ("repairable", "transient")]
        if not failed:
            break

        feedback = render_feedback(failed, mcp.tools_by_name)
        logger.warning(
            f"第 {round_no} 轮有 {len(failed)} 个工具调用失败，回灌模型重新决策"
        )
    else:
        logger.error("修复次数耗尽，仍有工具调用失败")

    return render_results(outcomes)
```

`render_feedback` 是这里最要紧的函数，**内容必须具体到模型能照着改**：

```
你上一轮的工具调用失败了，请修正后重新调用。

工具名：search_project
你传的参数：{"owner_id": "aaaaaaaa-...", "status": 1}
错误信息：ValidationError: 参数 status 类型不符，期望 string，收到 integer

该工具的正确参数定义：
{"properties": {"owner_id": {"type": "string"}, "status": {"type": "string", ...}}, "required": [...]}
```

只给错误信息、不给 schema，模型经常改不对 —— 这是这个方案里投入产出比最高的一个细节。

**成功的那部分不要重跑。** 如果第一轮 `search_project` 成功、`search_employees` 参数错了，第二轮只重新决策失败的那个。原因见下面第 4 节。

### Step 5 —— 收口 `graph/node.py::tool_use`

`node.py:360-384` 简化成薄薄一层：

```python
async def tool_use(state: State):
    tool_use_times = state.get('tool_use_times', 0) + 1
    logger.info(f'第{tool_use_times}次调用工具')
    question = state.get('question', '') + '\n' + state.get('tool_result', '')

    try:
        results_text = await run_tools(question, format_history(state))
    except Exception as e:
        # 兜底：让图还能继续走到 generate_by_tool，而不是整个请求 500
        logger.exception(f"工具链路失败：{e}")
        results_text = f"工具调用失败：{e}"

    return {'tool_result': results_text, 'tool_use_times': tool_use_times}
```

顺带修掉 `node.py:364-367` 的既有 bug：

```python
try:
    mcp_client = get_mcp_client()
except Exception as e:
    logger.exception(f"获取MCP客户端失败：{e}")
# ← 这里没有 return/raise，下面直接 await mcp_client.get_catalog_text()
```

`get_mcp_client()` 一旦失败，`mcp_client` 要么未绑定（`UnboundLocalError`），要么用上一轮的**陈旧值**，两种情况都不是你想要的。改成让异常往上抛，由上面的兜底接住。

### Step 6 —— 新增配置项（`app/config.py`）

按现有风格（裸 `os.getenv` + 默认值）加：

```python
# --- MCP 工具调用容错 ---
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8001/mcp")
MCP_CALL_TIMEOUT_SECONDS = float(os.getenv("MCP_CALL_TIMEOUT_SECONDS", "15"))
MCP_INIT_TIMEOUT_SECONDS = float(os.getenv("MCP_INIT_TIMEOUT_SECONDS", "10"))

# 瞬时故障的退避重试
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "4"))
RETRY_BASE_DELAY = float(os.getenv("RETRY_BASE_DELAY", "0.5"))
RETRY_MAX_DELAY = float(os.getenv("RETRY_MAX_DELAY", "8"))

# 参数/格式错误的回灌修复轮数（0 = 关闭修复，退回现状行为）
MAX_REPAIR_ROUNDS = int(os.getenv("MAX_REPAIR_ROUNDS", "2"))
MAX_DECISION_ATTEMPTS = int(os.getenv("MAX_DECISION_ATTEMPTS", "3"))
```

把 `MCP/server.py:353` 里硬编码的 URL 也一起挪过来，两个进程共用同一个配置项。

`MAX_REPAIR_ROUNDS=0` 能一键退回现状，方便做 A/B 对比。

---

## 4. 必须一起处理的三个坑

这三个不处理，上面的东西要么白做，要么引入新 bug。

### 4.1 写操作不能盲目重试 —— `create_ticket`

`MCP/server.py:230` 的 `create_ticket` 是**非幂等的写操作**。读超时不代表没执行成功 —— 请求可能已经落库，只是响应没回来。盲目重试会**创建重复工单**。

建议区分两种瞬时故障：

- **确定没送达**（连接建立失败、`ConnectError`、`RemoteProtocolError` 在建连阶段）→ 安全，正常退避重试；
- **可能已送达**（读超时、响应阶段连接断开）→ 对读类工具照常重试；对写类工具**不自动重试**，而是把"状态未知"作为一条 `uncertain` 结果回灌给模型，让它在回答里提示用户去确认。

实现上最小改动是在 `tool_runner.py` 里维护一个集合：

```python
WRITE_TOOLS = {"create_ticket"}   # 非幂等，重试策略要更保守
```

（更彻底的方案是让 `create_ticket` 接受一个 `idempotency_key`，服务端去重。这属于改造 MCP server 的范畴，可以后面单独做。）

### 4.2 空结果不是失败

`node.py:374-379` 已经正确处理了 `r.content` 为空的情况。**这一点在加重试的时候特别容易被破坏**：如果 `is_error=False` 但结果为空也被当成失败去重试，"查询张某某的员工信息、查不到"就会白烧 4 次调用，然后修复循环再让模型改参数、再烧 4 次 —— 最后模型还可能被逼着编造一个员工出来。

**明确规则：`is_error=False` 且 `content` 为空 → `kind="ok"`，交付给 `generate_by_tool`，绝不重试。**

### 4.3 `get_mcp_client()` 是个存了可变状态的单例

`client.py:138-141` 的 `@lru_cache` 单例上，`client.py:108` 把 `self.toolcall_decision` 存成了实例属性。**并发请求会互相覆盖对方的决策结果** —— 请求 A 执行到一半，请求 B 把 `toolcall_decision` 换掉了，A 就会拿着 B 的工具列表去执行。

这是既有问题，但**加重试会显著拉长这个窗口**（一次调用从 200ms 变成可能的 30s+），把偶发竞态变成必现 bug。

最小修复：把 `toolcall_decision` 从实例属性改成 `make_toolcall_decision()` 的**返回值**，由 `tool_use` / `tool_runner` 持有并显式传给 `call_tool(decision)`。改动很小，而且正好和 Step 3/4 是同一片代码，一起做掉最省事。

---

## 5. 怎么验证

退避和修复循环都属于"平时看不出来、出事时才生效"的代码，必须有办法主动注入故障，否则改完了也不知道对不对。

### 5.1 在 MCP server 加故障注入（仅调试用）

`MCP/server.py` 里加一个环境变量控制，默认关闭：

```python
# 仅用于验证重试链路。形如 "search_project:timeout:2"，
# 表示 search_project 的前 2 次调用故意睡过超时阈值。
```

这样能精确验证：退避间隔是否符合预期、第 N 次是否成功、日志是否打了 attempt 号。

### 5.2 独立的验证脚本

`MCP/client.py:143-159` 已经有一个 `main()` 直接跑客户端、不经过图的先例。照这个模式加一个脚本，覆盖四个场景：

| 场景 | 注入 | 预期 |
|---|---|---|
| 瞬时抖动后成功 | 前 2 次调用超时 | 退避 2 次后成功，日志有 attempt 号，最终结果正常 |
| 抖动一直存在 | 始终超时 | 重试到上限后放弃，不 hang 死，返回可读的失败说明 |
| 参数写错 | 提供不存在的参数名 | 一轮回灌后模型改用正确参数并成功 |
| 查不到数据 | 正常调用、返回空 | **调用次数为 1**，无任何重试 |

最后一行是防回归的关键 —— 它保证 4.2 那条规则没被破坏。

### 5.3 端到端

用一个会触发工具分支的问题（比如"查询 id:... 负责的项目"）走一遍 `POST /chat`，确认 `tool_result` 正常、`generate_by_tool` 拿到的内容是对的。项目里有 `eval/` 目录和 `test_cases.jsonl`，可以顺手给工具分支补几条用例。

---

## 6. 工作量与优先级

| Step | 内容 | 依赖 | 预估 |
|---|---|---|---|
| 1 | 补超时 | 无 | 0.5h |
| 2 | `core/retry.py` 分类器 + 退避 | 无 | 1.5h |
| 3 | 改造 `MCP/client.py` | 1, 2 | 1.5h |
| 4 | `MCP/tool_runner.py` 修复循环 | 3 | 2h |
| 5 | `tool_use` 收口 + 修 unbound bug | 4 | 0.5h |
| 6 | 配置项 | 无 | 0.5h |
| 4.3 | 单例竞态（建议同期做） | 3 | 1h |
| 5.1/5.2 | 故障注入 + 验证脚本 | 全部 | 2h |

**建议的落地顺序**：Step 1 + 2 + 3 先做，这一步就能解决"网络抖动导致整个请求挂死/崩溃"的问题，是收益最大的一段。跑通验证脚本确认退避生效后，再做 Step 4 的修复循环。

**如果只想做一半**：做 Step 1 + 2 + 3 + 5。这三步覆盖了第一类失败（抖动），并修掉 `toolcall_decision=None` 和 `mcp_client` 未绑定这两个确定性崩溃点。第二类失败（参数回灌）虽然是你更想要的那个，但它依赖的修复循环逻辑更复杂、更容易引入新问题，值得单独一个迭代来做扎实。
