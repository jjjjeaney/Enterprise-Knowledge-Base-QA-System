import asyncio
import random
from typing import Literal
import anyio, httpx
from fastmcp.exceptions import ToolError
from loguru import logger
from mcp.shared.exceptions import MCPError

from core.config import RETRY_BASE_DELAY, RETRY_MAX_DELAY

Kind = Literal["transient", "repairable", "uncertain", "fatal"]

# 按异常类型判瞬时故障的兜底。httpx.TransportError 是伞类，覆盖 ConnectError /
# ReadTimeout / ConnectTimeout / RemoteProtocolError / ReadError / PoolTimeout；
# 3.12 里 asyncio.TimeoutError 就是内置 TimeoutError。
#
# ★ 注意这一组**接不住 MCP 的故障**：fastmcp 自带的是 httpx2，一个独立的包
#   （site-packages/httpx2/），实测 httpx.TransportError is httpx2.TransportError
#   为 False，而且 fastmcp 还会把原始异常包进 ExceptionGroup 再重新抛出，类型
#   基本丢光。MCP 那条路走的是 McpSessionFailure 的阶段判断。
#
#   保留这一组是给**模型调用**用的：langchain / openai SDK 走的是真正的 httpx，
#   那里按类型判断是对的。
_TRANSIENT = (
    httpx.TransportError,
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    TimeoutError,
)

# 非幂等的写操作。重发可能造成重复写入：读超时不代表没执行成功，
# 请求可能已经落库、只是响应没回来。
WRITE_TOOLS = {"create_ticket"}


class ToolFailure(Exception):
    """单个工具调用最终失败：退避耗尽，或错误本身不值得重试。"""

    def __init__(
        self,
        kind: Kind,
        tool_name: str,
        arguments: dict,
        cause: BaseException,
        attempts: int = 1,
    ):
        super().__init__(f"{tool_name}({arguments}) 调用失败[{kind}]：{cause!r}")
        self.kind = kind
        self.tool_name = tool_name
        self.arguments = arguments
        self.cause = cause
        self.attempts = attempts

class McpSessionFailure(Exception):
    """MCP 会话层失败 —— 传输/握手出了问题，不是工具本身报错。

    ★ 为什么要专门造这一类，而不是直接认 httpx 的异常类型：
      fastmcp 会把底层的 httpx.ConnectError / ConnectTimeout / ReadTimeout /
      RemoteProtocolError 包进 anyio 的 ExceptionGroup，再重新抛一个**丢失了
      原始类型**的 RuntimeError 或 MCPError。实测确认：递归拆开 ExceptionGroup
      后拿到的叶子异常也不再是 httpx 类型，所以"这是不是 httpx.TransportError"
      恒为 False —— 靠类型判断瞬时故障会全部落空。

      既然类型信息没了，就只能按**失败发生在哪一步**来判。下面两个子类分别
      对应握手段和调用段，这个划分不受 fastmcp 内部实现变化的影响。
    """

    def __init__(self, tool_name: str | None = None, arguments: dict | None = None):
        super().__init__(f"MCP 会话失败（tool={tool_name}, arguments={arguments}）")
        self.tool_name = tool_name
        self.arguments = arguments

    def __str__(self) -> str:
        # __cause__ 是 raise ... from 挂上去的，构造时还拿不到，所以只能在这里拼。
        # 不拼的话，这个异常被渲染成用户可见的"工具调用失败：…"时会丢掉底层原因
        # —— 只剩"MCP 会话失败（tool=None, arguments=None）"，
        # 而"Failed to initialize server session"恰恰是排查时唯一有用的信息。
        base = super().__str__()
        if self.__cause__ is not None:
            return f"{base}，原因：{self.__cause__}"
        return base


class McpConnectFailure(McpSessionFailure):
    """握手没完成 —— 工具一定没有被调用过。

    `async with self.client` 的 __aenter__ 里做的就是 MCP initialize 握手，
    它失败意味着连接压根没建起来，工具不可能执行。所以这一类即使对
    create_ticket 这种非幂等的写操作，重发也是安全的。
    """


class McpInvokeFailure(McpSessionFailure):
    """握手成功之后才失败 —— 工具可能已经执行了，只是响应没回来。

    读操作照常重试；写操作重发会造成重复写入，要如实上报"结果不确定"。
    """


# 能确定"请求没有送达服务端"的异常。
# McpConnectFailure 是主判据；httpx 那两个留作兜底，万一将来 fastmcp 升级后
# 改成透传原始异常，这里也能接住。
_NEVER_SENT = (
    McpConnectFailure,
    httpx.ConnectError,
    httpx.ConnectTimeout,
)


# 服务端明确回绝了请求 —— 说明"路走错了"，重发一百次结果一样。
# 判据必须是 MCP 错误码，不能是异常类型：fastmcp 把 HTTP 状态码折叠成了
# JSON-RPC 码，实测 404 变成 -32601。
#
# ★ 这套判断只能覆盖一部分故障，别指望它包打天下：
#   401 / 405 / 500 都被折叠成同一个 -32603，区分不出来，所以那几种仍然
#   按瞬时故障重试。真正的解法是把配置类错误挡在启动时（见启动探活），
#   而不是在请求路径上猜 —— 能走到这里的失败，本来就应该大多是瞬时的。
_FATAL_MCP_CODES = {
    -32601,  # Method not found：端点上没有这个方法，多半是 URL 路径不对
    -32602,  # Invalid params：请求本身构造错了，重发不会变好
}


def is_fatal_endpoint_error(exc: BaseException) -> bool:
    """服务端回了响应、但明确说这个请求它不认。

    这类错误的特点是：**连接是通的**，问题出在"我们问错了地方"。重试没有
    意义，只会让每次请求都白等几秒退避，还把日志刷成一片"服务端暂时不可用"，
    把真正的问题（URL 配错）藏起来。
    """
    if not isinstance(exc, MCPError):
        return False
    # MCPError 的 code 有时在 exc.code，有时只在 exc.error.code
    code = getattr(exc, "code", None)
    if code is None:
        code = getattr(getattr(exc, "error", None), "code", None)
    return code in _FATAL_MCP_CODES



def classify(exc: BaseException, *, where: str) -> Kind:
    """判断这个异常属于哪一类，决定该退避重试、回灌模型、还是直接放弃。

    ★ 主判据是"失败发生在哪一步"（McpSessionFailure 的两个子类），不是异常
      类型 —— 因为 fastmcp 已经把底层类型抹掉了，详见 McpSessionFailure 的
      说明。下面的 `where` 和 httpx 类型判断都是兜底。

    ★ 另一个容易踩的点：同一个异常类在不同调用点含义不同。
      ToolError 出现在 call_tool 是"参数写错了"（repairable），
      出现在建连阶段则是"服务端有问题"（transient）。
    """
    if isinstance(exc, McpSessionFailure):
        # 握手或传输没走通，等一会儿重连就可能好。
        # 至于"是不是确定没送达"，由 _NEVER_SENT 另判，不影响这里的分类。
        return "transient"
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


# 工具调用退避重试
async def call_with_backoff(mcp, tool_name, arguments, *, max_attempts):
    """反复调用单个工具，直到成功、或确定重试没用为止。

    ★ 重试单位是"重建会话 → 调工具"整个块，不是单独重发一次 call_tool：
      底层会话一旦坏了，在同一个坏会话上重发只会连续失败。
      这正是 mcp.call_one 每次自己开 async with 的原因。

    成功返回 (result, attempts)；最终失败抛 ToolFailure。

    只 catch Exception 而不是 BaseException：后者会把 asyncio.CancelledError
    一起吞掉，客户端断开后这个协程会继续傻跑完重试。
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await mcp.call_one(tool_name, arguments), attempt
        except Exception as exc:
            kind = classify(exc, where="call_tool")

            if kind != "transient":
                raise ToolFailure(kind, tool_name, arguments, exc, attempt) from exc

            if tool_name in WRITE_TOOLS and not isinstance(exc, _NEVER_SENT):
                # 写操作 + 请求可能已经送达：重发会重复建单。
                # 如实上报"不确定"，把是否重试的决定权交回给用户。
                raise ToolFailure("uncertain", tool_name, arguments, exc, attempt) from exc

            if attempt == max_attempts:
                raise ToolFailure(kind, tool_name, arguments, exc, attempt) from exc

            delay = backoff_delay(attempt)
            logger.warning(
                "工具调用遇到瞬时故障，退避后重试 tool={} attempt={}/{} delay_s={:.2f} "
                # 用 str 而不是 repr：McpSessionFailure.__str__ 会带上底层原因，
                # 而 repr 只打印我们自己的那层壳，把真正有用的信息丢掉。
                "kind={} error={}",
                tool_name,
                attempt,
                max_attempts,
                delay,
                type(exc).__name__,
                exc,
            )
            await asyncio.sleep(delay)
