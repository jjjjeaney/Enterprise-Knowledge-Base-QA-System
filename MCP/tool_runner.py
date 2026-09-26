import asyncio
import json
import time
from dataclasses import dataclass

from loguru import logger

from core.config import (
    MAX_REPAIR_ROUNDS,
    RETRY_MAX_ATTEMPTS,
    TOOL_TOTAL_BUDGET_SECONDS,
)
from core.retry import ToolFailure, call_with_backoff
from MCP.client import ToolCall, ToolCallDecision, get_mcp_client

# 已经有确定结果的结果类型，不会再重跑。
# 注意 "empty" 在这里 —— 查不到数据是合法结果，不是失败。
_SETTLED = ("ok", "empty")

# 值得回灌给大模型重来一轮的失败。transient 也在内：退避重试已经失败过了，
# 但让模型换个思路再试一次可能有用。uncertain 不在内 —— 见 _KIND_LABEL。
_REPAIRABLE = ("repairable", "transient")

# 每种失败在最终结果里的说明。这段文字是给 generate_by_tool 看的：
# 它必须能分清"没查到"和"没调成功"，否则容易把失败当成空结果去编内容。
_KIND_LABEL = {
    "repairable": "调用失败：大模型给出的参数不符合该工具要求，修正后重试仍然失败",
    "transient": "调用失败：服务端暂时不可用，退避重试多次后仍然失败",
    "uncertain": "结果不确定：请求超时，该操作可能已经生效。不要直接重试，"
                 "请提示用户自行到系统中确认",
    "fatal": "调用失败：该错误无法通过重试解决",
}


@dataclass
class ToolOutcome:
    """单个工具这一次的最终归宿。"""

    tool_name: str
    arguments: dict
    text: str
    kind: str  # ok | empty | repairable | transient | uncertain | fatal
    attempts: int = 1


def _content_text(result) -> str:
    """把 CallToolResult 的 content 拼成纯文本。

    content 是内容块列表而不是字符串；空列表代表工具正常返回但没数据。
    """
    parts = []
    for block in (getattr(result, "content", None) or []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


async def _execute_one(mcp, tool_call: ToolCall, *, max_attempts: int) -> ToolOutcome:
    tool_name = tool_call.tool_name
    arguments = tool_call.arguments or {}

    try:
        result, attempts = await call_with_backoff(
            mcp, tool_name, arguments, max_attempts=max_attempts
        )
    except ToolFailure as f:
        logger.warning(
            "工具调用最终失败 tool={} kind={} attempts={} cause={!r}",
            tool_name,
            f.kind,
            f.attempts,
            f.cause,
        )
        return ToolOutcome(tool_name, arguments, str(f.cause), f.kind, f.attempts)
    except Exception as e:
        # call_with_backoff 只会抛 ToolFailure，这里纯属兜底，
        # 保证单个工具的意外不会掀翻 asyncio.gather 里的其他工具。
        logger.exception("工具调用出现未预期异常 tool={}", tool_name)
        return ToolOutcome(tool_name, arguments, repr(e), "fatal")

    # raise_on_error=False，所以参数校验这类工具层错误是 is_error=True 的结果
    # 而不是异常。它们不会自己变好，必须回灌给模型改参数。
    if getattr(result, "is_error", False):
        return ToolOutcome(tool_name, arguments, _content_text(result), "repairable", attempts)

    text = _content_text(result)
    if not text:
        # 空结果是合法结果，绝不能重试：否则"查无此人"会被反复重查，
        # 修复循环还会逼着模型改参数，最后可能被逼出一个不存在的员工。
        return ToolOutcome(tool_name, arguments, "没有查询到任何记录", "empty", attempts)

    return ToolOutcome(tool_name, arguments, text, "ok", attempts)


async def execute_all(
    mcp, tool_calls: list[ToolCall], *, max_attempts: int = RETRY_MAX_ATTEMPTS
) -> list[ToolOutcome]:
    """并发执行本轮的所有工具。工具之间互不影响，一个失败不拖垮整批。"""
    if not tool_calls:
        return []
    return list(
        await asyncio.gather(*(
            _execute_one(mcp, tc, max_attempts=max_attempts) for tc in tool_calls
        ))
    )


def render_feedback(failed: list[ToolOutcome], tools_by_name: dict) -> str:
    """把失败信息组织成下一轮决策的输入。

    ★ 必须带上该工具的正确参数定义。只丢一句"参数错了"模型经常改不对，
      而把 input_schema 原样贴给它，几乎一次就能改对 —— 这是整条修复链路上
      投入产出比最高的一个细节。
    """
    blocks = []
    for o in failed:
        tool = tools_by_name.get(o.tool_name)
        if tool is None:
            hint = (
                "该工具不在可用工具列表中，请从可用工具里重新选择。"
                f"可用工具：{', '.join(tools_by_name) or '（列表为空）'}"
            )
        else:
            hint = "该工具的正确参数定义：\n" + json.dumps(
                tool.input_schema, ensure_ascii=False, indent=2
            )

        retry_hint = (
            "刚才只是服务端瞬时故障，参数本身没问题，可以原样重试。"
            if o.kind == "transient"
            else "请修正参数后重新调用。"
        )

        blocks.append(
            f"工具名：{o.tool_name}\n"
            f"你上一轮传的参数：{json.dumps(o.arguments, ensure_ascii=False)}\n"
            f"错误信息：{o.text}\n"
            f"{hint}\n"
            f"{retry_hint}"
        )

    return "你上一轮的工具调用失败了，请修正后重新调用。\n\n" + "\n\n".join(blocks)


def render_results(outcomes: list[ToolOutcome]) -> str:
    """渲染成 generate_by_tool 消费的文本。字段格式沿用改造前的 tool_use。"""
    if not outcomes:
        return "（本轮没有需要调用的工具）"

    blocks = []
    for o in outcomes:
        if o.kind in _SETTLED:
            text = o.text
        else:
            label = _KIND_LABEL.get(o.kind, "调用失败")
            text = f"{label}（原始错误：{o.text}）"
        blocks.append(
            f"\ntool_name:{o.tool_name}\narguments:{o.arguments}\ntool_result:{text}\n"
        )
    return "".join(blocks)


async def run_tools(question: str, history: str) -> str:
    """工具分支的入口：决策 → 执行 → 失败则回灌模型重新决策。

    这是"格式/参数错误回灌大模型"的落点。修复循环做在节点内部，而不是靠
    外层图循环（routes.py 的 route_after_check_tool_result）：工具一旦抛异常
    节点就中断了，根本走不到 check_tool_result；而且走一圈图要重跑
    check_tool_result + generate_by_tool，代价远高于在这里重来一轮。
    """
    mcp = get_mcp_client()
    try:
        await mcp.get_catalog_text()
    except Exception as e:
        logger.exception("工具目录加载失败，本轮工具分支不可用")
        return render_results([ToolOutcome("", {}, repr(e), "fatal")])

    settled: list[ToolOutcome] = []  # 已拿到确定结果（含"查无数据"），不再重跑
    failed: list[ToolOutcome] = []
    feedback = ""  # 上一轮的错误，作为下一轮决策的输入
    deadline = time.monotonic() + TOOL_TOTAL_BUDGET_SECONDS

    for round_no in range(1, MAX_REPAIR_ROUNDS + 2):
        try:
            decision: ToolCallDecision = await mcp.make_toolcall_decision(
                history, question, feedback
            )
        except Exception as e:
            # 决策连续失败。这里不能往外抛 —— 抛出去整个请求就 500 了，
            # 而正确答案往往只是"这次没有工具能用"。
            logger.exception("工具决策失败，放弃本轮工具")
            failed = [ToolOutcome("", {}, repr(e), "fatal")]
            break

        # 已经拿到结果的工具不再重跑。这条不只是省时间：create_ticket 是
        # 非幂等的写操作，重跑会重复建单。
        done = {o.tool_name for o in settled}
        pending = [tc for tc in (decision.tool_calls or []) if tc.tool_name not in done]
        if not pending:
            break

        outcomes = await execute_all(mcp, pending)
        settled.extend(o for o in outcomes if o.kind in _SETTLED)
        failed = [o for o in outcomes if o.kind not in _SETTLED]

        repairable = [o for o in failed if o.kind in _REPAIRABLE]
        if not repairable:
            # 剩下的即使有失败，也是重试和改参数都救不回来的（uncertain/fatal）。
            break

        # 修复轮数和退避重试是叠乘的，用总时长兜住，别让一次请求无限等下去。
        if time.monotonic() >= deadline:
            logger.error(
                "工具调用超出总时长预算，停止修复 budget_s={} round={}",
                TOOL_TOTAL_BUDGET_SECONDS,
                round_no,
            )
            break

        feedback = render_feedback(repairable, mcp.tools_by_name)
        logger.warning(
            "有工具调用失败，回灌模型重新决策 round={} failed={}",
            round_no,
            len(repairable),
        )
    else:
        logger.error(
            "修复轮数耗尽，仍有工具调用失败 max_repair_rounds={}", MAX_REPAIR_ROUNDS
        )

    return render_results(settled + failed)
