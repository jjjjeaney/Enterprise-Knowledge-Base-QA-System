import asyncio
import sys
from datetime import date
from typing import Dict, Any, List
from functools import lru_cache
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from langchain_core.messages import AIMessage, HumanMessage
from core.prompts import ROUTING_PROMPT
from pydantic import BaseModel, Field

from core.config import (
    MAX_DECISION_ATTEMPTS,
    MCP_CALL_TIMEOUT_SECONDS,
    MCP_INIT_TIMEOUT_SECONDS,
    MCP_SERVER_URL,
    RETRY_MAX_ATTEMPTS,
)

from loguru import logger

from core.llm import get_deepseek_chat_llm
from core.retry import (
    McpConnectFailure,
    McpInvokeFailure,
    backoff_delay,
    classify,
    is_fatal_endpoint_error,
)


def _today_iso() -> str:
    return date.today().isoformat()


class ToolCall(BaseModel):
    tool_name: str = Field(
        ...,
        description="Name of the MCP tool to call"
    )
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Keyword arguments for the MCP tool"
    )


class ToolCallDecision(BaseModel):
    tool_calls: List[ToolCall]






class MCPClient():
        def get_client(self) -> Client:
            sys.stdout.reconfigure(encoding='utf-8', errors='replaces')
            TRANSPORT = StreamableHttpTransport(url=MCP_SERVER_URL)
            return Client(
                transport=TRANSPORT,
                timeout=MCP_CALL_TIMEOUT_SECONDS,  # 单次工具调用
                init_timeout=MCP_INIT_TIMEOUT_SECONDS,  # 握手
            )

        # 工具列表同样要走退避：server 还没起来时这一步会最先炸
        async def list_tools_with_backoff(self, max_attempts: int = RETRY_MAX_ATTEMPTS):
            for attempt in range(1, max_attempts + 1):
                try:
                    # 每次尝试都开新会话，理由同 call_one
                    async with self.client as client:
                        return await client.list_tools()
                except Exception as e:
                    # 服务端回了响应、但说这个请求它不认（URL 路径不对之类）：
                    # 重发没意义。原样抛出即可 —— classify 的默认返回值就是
                    # "fatal"，不需要再包一层类型。
                    if is_fatal_endpoint_error(e):
                        raise
                    # 列工具没有"参数"可改，会话失败只可能是服务端本身的问题，
                    # 统一标成握手失败 —— 这也是"确定没送达"，重发是安全的。
                    # 注意不能再用 classify(where="list_tools") 过滤：fastmcp
                    # 会把 httpx 异常的类型抹掉，判出来清一色是 fatal，等于不退避。
                    if attempt == max_attempts:
                        raise McpConnectFailure() from e
                    delay = backoff_delay(attempt)
                    logger.warning(
                        "获取工具列表失败，退避后重试 attempt={}/{} delay_s={:.2f} error={!r}",
                        attempt,
                        max_attempts,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)

        async def get_catalog_text(self):
            if self.catalog_text is not None:
                return self.catalog_text
            tools = await self.list_tools_with_backoff()  # 同样按 transient 退避
            self.tools_by_name = {t.name: t for t in tools}  # ← 新增，供回灌时取 schema
            self.catalog_text = "\n".join(
                f"- {t.name}: {t.description}\n  参数: {t.input_schema}"
                for t in tools
            )
            return self.catalog_text

        def __init__(self):
            self.client:Client= self.get_client()
            self.catalog_text:str|None=None
            self.toolcall_decision:ToolCallDecision|None=None
            self.llm=get_deepseek_chat_llm()
            self.tools_by_name={}

        async def call_one(self, tool_name: str, arguments: dict):
            """开一个新会话，只调一个工具。

            ★ 每次调用都新建会话，是为了让"重连后重试"真的成立 ——
              复用同一个已经坏掉的会话重发，只会连续失败。

            raise_on_error=False：参数校验之类的工具层错误会以
            is_error=True 的结果返回（由上层分类成 repairable，回灌给模型），
            只有传输层故障才抛异常，交给退避重试。

            ★ connected 标志用来区分"握手失败"和"调用失败"。fastmcp 把底层
              httpx 异常的类型抹掉了（见 core.retry.McpSessionFailure），所以
              只能靠"走到哪一步"来判。这个区别决定写操作能不能安全重发：
              握手都没成，create_ticket 一定没执行过，重发不会重复建单。
            """
            connected = False
            try:
                async with self.client as client:
                    connected = True  # 走到这行 = 握手成功
                    return await client.call_tool(
                        tool_name,
                        arguments,
                        raise_on_error=False,
                    )
            except Exception as e:
                # 服务端回了响应、但说这个请求它不认（URL 路径不对之类）：
                # 连接是通的，问题在"问错了地方"，重发一百次结果一样。
                # 原样抛出即可 —— classify 的默认返回值就是 "fatal"。
                if is_fatal_endpoint_error(e):
                    raise
                raise (McpInvokeFailure if connected else McpConnectFailure)(
                    tool_name, arguments
                ) from e

        async def make_toolcall_decision(self, history, question, error_feedback=""):
            llm_with_structured=get_deepseek_chat_llm().with_structured_output(ToolCallDecision)
            messages = ROUTING_PROMPT.format_messages(
                today_date=_today_iso(),
                question=question,
                tool_catalog=self.catalog_text,
                history=history,
                error_feedback=error_feedback
            )
            for attempt in range(1, MAX_DECISION_ATTEMPTS + 1):
                try:
                    self.toolcall_decision: ToolCallDecision = await llm_with_structured.ainvoke(messages)
                    return self.toolcall_decision
                except Exception as e:
                    kind = classify(e, where="llm_structured")
                    if kind != "repairable" or attempt == MAX_DECISION_ATTEMPTS:
                        raise
                    # 把校验错误本身变成下一轮的输入
                    messages = messages + [
                        AIMessage(content=repr(getattr(e, "raw_output", ""))),
                        HumanMessage(content=f"上次输出无法解析，错误如下，请重新输出：\n{e}"),
                    ]

@lru_cache
def get_mcp_client():
    mcp_client = MCPClient()
    return mcp_client

async def main():
    client:MCPClient=get_mcp_client()
    await client.get_catalog_text()
    await client.make_toolcall_decision([],"帮我查询由id：aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa负责的项目")
    results= await client.tool_call()
    tool_result=[]
    for r,t in zip(results,client.toolcall_decision.tool_calls):
       result_text=f"tool_name:{t.tool_name}\narguments:{t.arguments}\ntool_result:{r.content[0].text}"
       tool_result.append(result_text)
    print('\n\n'.join(tool_result))
    print(results)




if __name__ == '__main__':
    asyncio.run(main())

