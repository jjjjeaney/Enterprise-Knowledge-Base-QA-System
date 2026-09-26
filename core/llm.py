import asyncio
import random
from functools import lru_cache

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from loguru import logger
import openai
from langchain.chat_models import init_chat_model
from openai import APIConnectionError, APITimeoutError
from pydantic import ValidationError

from core.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL, \
    LLM_RETRY_BASE_DELAY, LLM_RETRY_MAX_DELAY


@lru_cache
def get_deepseek_chat_llm():
    return init_chat_model(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model='deepseek-v4-flash',
        extra_body={"thinking": {"type": "disabled"}},
        max_tokens=1024,
        timeout=60
    )
@lru_cache
def get_deepseek_vision_llm():
    return init_chat_model(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model='deepseek-v4-flash-vision-exp',
        timeout=60
    )
@lru_cache
def get_openai_chat_llm():
    return init_chat_model(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        model='gpt-5.4-mini',
        extra_body={"thinking": {"type": "disabled"}},
        max_tokens=1024,
        timeout=60
    )





class LLMError(Exception):
    pass
class RetryableError(LLMError):
    pass
class NonRetryableError(LLMError):
    pass


# network
class RetryConnectionError(RetryableError):
    pass
class DNSResolutionError(NonRetryableError):
    pass


# api
class AuthenticationError(NonRetryableError):
    pass
class BadRequestError(NonRetryableError):
    pass
class RateLimitError(RetryableError):
    pass
class ServerError(RetryableError):
    pass

# response
class EmptyContentError(RetryableError):
    pass
class OutputSchemaError(NonRetryableError):
    def __init__(self, feedback:str):
        self.feedback=feedback
    def __str__(self):
        return self.feedback
class TokenLimitError(NonRetryableError):
    pass
class UnknownError(LLMError):
    pass


def classify(exc):
    """把 SDK 抛出的异常归类成重试/不重试两类。

    ★ 刻意不在这里打日志。它是被 `invoke_llm` 在 except 分支里调用的纯分类函数，
      日志留在这里会让同一个异常在 classify / invoke_with_backoff /
      llm_invoke_gateway 各打一遍，而且级别还互相矛盾（分类层按异常类型选级别，
      但 TokenLimitError 这类到了 gateway 层其实会被回灌修复，根本不是错误）。
      记录统一放在真正的边界上：重试打 WARNING，最终失败打 ERROR。
    """
    # client
    if isinstance(exc,(ValidationError, ValueError, NotImplementedError)):
        return NonRetryableError(str(exc))

    # network
    if isinstance(exc,(APIConnectionError, APITimeoutError)):
        return RetryConnectionError(str(exc))

    # api
    if isinstance(exc,openai.AuthenticationError):
        return AuthenticationError(str(exc))

    if isinstance(exc,openai.BadRequestError):
        return BadRequestError(str(exc))

    if isinstance(exc,openai.RateLimitError):
        return RateLimitError(str(exc))

    if isinstance(exc,openai.InternalServerError):
        return ServerError(str(exc))

    return UnknownError(str(exc))




async def invoke_llm(llm,messages:list):

    llm_name=getattr(llm, "model_name", "unknown") or llm.first.bound.model_name

    try:
        response = await llm.ainvoke(messages)
        if isinstance(response, BaseMessage):
            finish_reason = response.response_metadata.get("finish_reason")
            if finish_reason == "length":
                logger.warning("LLM 输出被 token 上限截断 model={}", llm_name)
                raise TokenLimitError()
            if response.content == "":
                logger.warning("LLM 返回空内容 model={}", llm_name)
                raise EmptyContentError()
        if isinstance(response, dict):
            if response['raw'].response_metadata['finish_reason']== "length":
                logger.warning("LLM 输出被 token 上限截断 model={}", llm_name)
                raise TokenLimitError()
            if response.get("parsing_error"):
                logger.warning(
                    "LLM 结构化输出解析失败 model={} error={}",
                    llm_name,
                    response["parsing_error"],
                )
                raise OutputSchemaError(str(response["parsing_error"]))
        logger.debug("LLM 调用成功 model={}", llm_name)
        return response
    except LLMError:
        raise
    except Exception as exc:
        # 不在这里记日志：分类结果和重试/修复决定在上层，日志由上层统一打，
        # 否则同一个异常会被打三遍（见 classify 的说明）。
        raise classify(exc) from exc

def backoff_delay(attempt: int) -> float:
    raw = min(LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1)), LLM_RETRY_MAX_DELAY)
    return raw * (0.5 + random.random() * 0.5)

async def invoke_with_backoff(llm,messages:list,max_attempts=3):
    """反复调用 LLM，退避重试到成功、或确定重试无用为止。

    ★ 放弃重试时**不在这里打日志**：TokenLimitError / OutputSchemaError 这类
      "不重试"的异常到了 llm_invoke_gateway 会被回灌模型修复，在这里按异常类型
      记 ERROR 会把一次成功修复写成错误。最终失败的 ERROR 由上层的边界打，
      这里只记重试过程。
    """
    attempt=0
    while True:
        try:
            attempt += 1
            return await invoke_llm(llm,messages)
        except Exception as exc:
            if (not isinstance(exc, RetryableError) or attempt >= max_attempts):
                raise
            delay = backoff_delay(attempt)
            logger.warning(
                "LLM 调用失败，退避后重试 attempt={}/{} delay_s={:.2f} kind={} error={}",
                attempt,
                max_attempts,
                delay,
                type(exc).__name__,
                exc,
            )
            await asyncio.sleep(delay)


async def llm_invoke_gateway(
    llm,
    messages:list,
    max_attempts:int=3,
    max_repair_times:int=2
):
    current_tokens = llm.max_tokens if hasattr(llm, "max_tokens") else llm.first.steps__['raw'].bound.max_tokens
    repair_times=0
    old_llm=llm
    new_llm=None
    while True:
        if new_llm is None:
            new_llm=old_llm
        try:
            return await invoke_with_backoff(new_llm,messages,max_attempts)
        except TokenLimitError:
            if repair_times <= max_repair_times:
                if isinstance(llm,ChatOpenAI):
                    # 记下旧值再扩容：不能读 new_llm.max_tokens，bind 出来的是
                    # RunnableBinding，那个属性第二轮会 AttributeError
                    old_tokens=current_tokens
                    current_tokens = min(current_tokens+1024,4096)
                    logger.warning(
                        "LLM 输出被 token 上限截断，扩容 max_tokens {} -> {}（第 {} 次）",
                        old_tokens,
                        current_tokens,
                        repair_times + 1,
                    )
                else:
                    # 结构化模型没法扩容，更适合提示词约束输出长度
                    logger.warning(
                        "LLM 结构化输出被 token 上限截断，回灌模型要求重新输出（第 {} 次）",
                        repair_times + 1,
                    )
                    messages.append(HumanMessage(
                            content=
                            "你上一次的输出被 token 上限截断、不完整。请重新输出一个完整、紧凑的 JSON，不要输出多余文字。"
                        )
                    )


                new_llm = old_llm.bind(max_tokens=current_tokens)
            else:
                logger.error(
                    "LLM 输出截断修复次数耗尽，放弃 max_repair_times={}", max_repair_times
                )
                raise
        except OutputSchemaError as exc:
            if repair_times <= max_repair_times:
                feedback = ("你上一次的输出结构存在错误，""错误信息：\n"+ str(exc))
                logger.warning(
                    "LLM 结构化输出不合规，回灌模型修复（第 {} 次）", repair_times + 1
                )
                messages.append(HumanMessage(content=feedback))
            else:
                logger.error(
                    "LLM 结构化输出修复次数耗尽，放弃 max_repair_times={}",
                    max_repair_times,
                )
                raise
        except Exception:
            # 走到这里的都是 invoke_with_backoff 放弃重试后抛上来的，它不记日志，
            # 所以这是最终失败的落点，要带堆栈。
            logger.exception(
                "LLM 调用最终失败 repair_times={} max_repair_times={}",
                repair_times,
                max_repair_times,
            )
            raise
        repair_times+=1