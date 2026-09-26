import asyncio
import time
from datetime import date
from typing import Literal

from langchain_core.messages import HumanMessage
from loguru import logger
from pydantic import BaseModel, Field

from db import get_session
from db.messages_repository import add_message
from db.models import Message
from MCP.tool_runner import run_tools
from core.llm import get_deepseek_chat_llm, llm_invoke_gateway
from core.reranker import get_reranker
from core.prompts import (
    CLASSIFY_PROMPT,
    DIRECT_PROMPT,
    RAG_GENERATION_PROMPT,
    REWRITE_PROMPT,
    TOOL_GENERATION_PROMPT,
    TOOL_VERIFY_PROMPT,
    VERIFY_ANSWER_PROMPT,
)
from graph.state import State
from retrieval.hybrid_retrieval import hybrid_retrieval

MAX_HISTORY_TURNS = 6
MAX_REWRITE_TIMES = 2
MAX_TOOL_USE_TIMES=5
# rerank 后最多保留的文档数
RERANK_TOP_K = 8
# 相关性阈值：score 低于该值的文档会被丢弃
RERANK_SCORE_THRESHOLD = 0.5
# 兜底阈值：一篇都过不了主阈值时，降到该阈值重筛，避免整题被一刀切筛空
RERANK_SCORE_THRESHOLD_FALLBACK = 0.15
llm=get_deepseek_chat_llm()

def format_history(state:State):
    history=state.get('chat_history',[])
    recent_history=history[-MAX_HISTORY_TURNS:]
    lines=[f'{turn['role']}:{turn['content']}' for turn in recent_history]
    return "Conversation so far:\n" + "\n".join(lines)

def _today_date():
    return date.today().isoformat()


class ClassifyQueryOutput(BaseModel):
    need_retrieval: bool = Field(..., description="Whether internal document retrieval is required")
    need_rewrite: bool = Field(..., description="Whether query rewriting is required")
    need_tool: bool = Field(..., description="Whether tool invocation is required")

async def classify_query(state:State):
    question=state['question']
    llm_with_structed=llm.with_structured_output(ClassifyQueryOutput,include_raw=True)
    messages=CLASSIFY_PROMPT.format_messages(history=format_history(state),question=question)
    try:
        response:ClassifyQueryOutput=(await llm_invoke_gateway(llm_with_structed,messages))['parsed']
        need_retrieval=response.need_retrieval
        need_rewrite=response.need_rewrite
        need_tool=response.need_tool
    except Exception:
        # 结构化输出解析失败（例如模型返回了"是/否"）不能让整次问答失败，保守退回改动前的行为
        logger.exception("查询分类失败，降级为不检索、不改写、不调用工具")
        need_retrieval=False
        need_rewrite=False
        need_tool=False
    query_type='retrieval_needed' if need_retrieval else 'general_knowledge'
    return{
        'need_rewrite':need_rewrite,
        'query_type': query_type,
        'need_retrieval': need_retrieval,
        'need_tool':need_tool,
        'rewrite_times':0,
        'original_question':question
    }

async def direct_generate_answer(state:State):
    history=format_history(state)
    question=state['question']
    messages=DIRECT_PROMPT.format(today_date=_today_date(),history=history,question=question)
    response=await llm_invoke_gateway(llm,messages)
    return{
        'final_answer':response.content
    }


# 根据问题进行混合检索,结果格式[{"id": xx,"content": xx,"metadata": {},"score": xx}]
async def enterprise_retrieve(state:State):
    question=state['question']
    # [{"id": xx,"content": xx,"metadata": {},"score": xx}]
    retrieval_result=await hybrid_retrieval(question,50,50,30)
    return {
        'retrieval_docs': retrieval_result
    }

# 检索后重排：对每个 (question, doc_content) 打分，按分数从高到低取前 top-k，
# 输出格式与原来的 grade_documents 保持一致（relevant_docs / rag_context / sources）。
async def rerank(state:State):
    question=state['question']
    retrieval_docs=state['retrieval_docs']

    # 检索本身就没结果时，走原来的"无相关文档"分支，让路由决定是否重写
    if not retrieval_docs:
        logger.warning("无候选文档，按无相关文档处理")
        return {
            'final_answer':'没有找到相关文档',
            'relevant_docs': [],
            'rag_context':'',
            'sources':[],
            'verification':None
        }

    reranker=get_reranker()
    pairs=[[question, d['content']] for d in retrieval_docs]
    # compute_score 是同步阻塞的，挪到线程池里跑，避免卡住事件循环
    start=time.perf_counter()
    scores=await asyncio.to_thread(reranker.compute_score, pairs, normalize=True)
    elapsed_ms=(time.perf_counter()-start)*1000

    # 分数与文档一一对应，按分数降序排列后取前 top-k
    scored=sorted(zip(retrieval_docs, scores), key=lambda x: x[1], reverse=True)

    # 逐篇分数是调阈值时的依据，属于细节，降到 DEBUG
    for rank, (doc, score) in enumerate(scored, start=1):
        preview=doc['content'].replace('\n', ' ')[:60]
        logger.debug(
            "rerank 排序 rank={} score={:.4f} source={} chunk_index={} | {}",
            rank,
            score,
            doc["metadata"]["source"],
            doc["metadata"]["chunk_index"],
            preview,
        )

    # 只保留分数达到阈值的文档，再取前 top-k
    passed=[doc for doc, score in scored if score >= RERANK_SCORE_THRESHOLD]
    top_docs=passed[:RERANK_TOP_K]
    logger.info(
        "rerank 完成 candidates={} passed={} kept={} threshold={} top_k={} elapsed_ms={:.0f}",
        len(retrieval_docs),
        len(passed),
        len(top_docs),
        RERANK_SCORE_THRESHOLD,
        RERANK_TOP_K,
        elapsed_ms,
    )

    # 一篇都没过主阈值时，用更宽松的兜底阈值重筛一次，避免阈值太严把整题筛空
    if not passed:
        logger.warning(
            "无文档达到主阈值，降级到兜底阈值重筛 threshold={} fallback={}",
            RERANK_SCORE_THRESHOLD,
            RERANK_SCORE_THRESHOLD_FALLBACK,
        )
        passed=[doc for doc, score in scored if score >= RERANK_SCORE_THRESHOLD_FALLBACK]
        top_docs=passed[:RERANK_TOP_K]

    # 兜底后仍无文档，按"无相关文档"处理，让路由决定是否重写
    if not top_docs:
        logger.warning(
            "兜底阈值后仍无文档通过，按无相关文档处理 fallback={}",
            RERANK_SCORE_THRESHOLD_FALLBACK,
        )
        return {
            'final_answer':'没有找到相关文档',
            'relevant_docs': [],
            'rag_context':'',
            'sources':[],
            'verification':None
        }

    # 生成rag_context与sources，格式与 grade_documents 一致
    rag_context=''
    sources=[]
    for d in top_docs:
        doc_content=d['content']
        source=d['metadata']['source']
        chunk_index=d['metadata']['chunk_index']
        rag_context+=f"id:{d['id']}\nfile_path:{source}\nchunk_index:{chunk_index}\ntext:{doc_content}\n\n\n"
        if source not in sources:
            sources.append(source)

    return {
        'rag_context': rag_context,
        'relevant_docs': top_docs,
        'sources':sources
    }


async def generate_by_rag(state:State):
    history=format_history(state)
    question=state['question']
    context=state['rag_context']
    messages=RAG_GENERATION_PROMPT.format(today_date=_today_date(),history=history,question=question,context=context)
    response=await llm_invoke_gateway(llm,messages)
    logger.info(
        "基于检索结果生成回答完成 context_chars={} answer_chars={}",
        len(context),
        len(response.content or ''),
    )

    return{
        'final_answer': response.content
    }

async def verify_answer(state: State):
    context = state.get("rag_context", "")
    question = state["question"]
    prompt = VERIFY_ANSWER_PROMPT.format(
        question=question,
        answer=state.get('final_answer', ''),
        context=context,
    )
    messages=[HumanMessage(content=prompt)]
    response = await llm_invoke_gateway(llm,messages)
    return {
        "verification": True if response.content=='YES' else False
    }


async def _rewrite_question(question:str,history:str):
    messages=REWRITE_PROMPT.format(today_date=_today_date(),question=question,history=history)
    response=await llm_invoke_gateway(llm,messages)
    rewritten=(response.content or '').strip()
    if not rewritten:
        logger.warning('查询改写返回空内容，回退使用原问题')
        return question
    return rewritten


# 检索前的改写：把追问补全成可以独立检索的查询。它不是失败重试，因此不占用 rewrite_times 预算
async def rewrite_query_standalone(state:State):
    history=format_history(state)
    question=state['question']
    rewritten=await _rewrite_question(question,history)
    logger.info(
        "检索前改写完成 chars_before={} chars_after={}", len(question), len(rewritten)
    )
    logger.debug("检索前改写内容 original={!r} rewritten={!r}", question, rewritten)
    return{
        'question': rewritten
    }


# 失败后的重写：相关性不达标或答案验证不通过时触发，计入 rewrite_times
async def rewrite_query_retry(state:State):
    rewrite_times = state.get('rewrite_times', 0) + 1
    history=format_history(state)
    question=state['question']
    rewritten=await _rewrite_question(question,history)
    logger.info(
        "重写问题完成 rewrite_times={}/{} chars_before={} chars_after={}",
        rewrite_times,
        MAX_REWRITE_TIMES,
        len(question),
        len(rewritten),
    )
    logger.debug("重写问题内容 original={!r} rewritten={!r}", question, rewritten)
    return{
        'question': rewritten,
        'rewrite_times':rewrite_times
    }

async def persist_conversation(state:State):
    original_question=state.get('original_question','')
    final_answer=state.get('final_answer','')
    # direct 分支和无相关文档分支不会写入这几个字段，这里取安全默认值
    sources=state.get('sources',[])
    thread_id=state.get('thread_id')
    verification=state.get('verification',None)
    logger.debug("保存会话消息 sources={} verification={}", len(sources), verification)
    async with get_session() as session:
        async with session.begin():
            session.add(Message(
                thread_id=thread_id,
                content=original_question,
                role="Human",
                sources=None,
                verification=None,
            ))

            session.add(Message(
                thread_id=thread_id,
                content=final_answer,
                role="AI",
                sources=sources,
                verification=verification,
            ))

# 根据问题调用工具
async def tool_use(state:State):
    tool_use_times=state.get('tool_use_times',0)+1
    logger.info("调用工具 tool_use_times={}/{}", tool_use_times, MAX_TOOL_USE_TIMES)
    question=state.get('question','')+'\n'+state.get('tool_result','')

    # 决策、退避重试、失败回灌都在 run_tools 里。这里只负责兜底：
    # 工具链路整个挂掉时不能让请求 500，要让图还能走到 generate_by_tool
    # 去说一句"没查到"，而不是给用户一个错误页。
    try:
        results_text = await run_tools(question, format_history(state))
    except Exception as e:
        # logger.exception 已经带上堆栈（含异常本身），不必再把 e 拼进消息
        logger.exception("工具链路失败，降级为无工具结果")
        results_text = f"\ntool_result:工具调用失败：{e}\n"

    logger.debug("工具调用结果 chars={}", len(results_text))
    return {
        'tool_result': results_text,
        'tool_use_times':tool_use_times
    }
# 根据工具调用结果生成回答
async def generate_by_tool(state:State):
    tool_result=state.get('tool_result','')
    question=state.get('question','')
    messages=TOOL_GENERATION_PROMPT.format_messages(
            today_date=_today_date(),
            chat_history=format_history(state),
            question=question,
            tool_result=tool_result
        )
    response=await llm_invoke_gateway(llm,messages)
    return {
        'final_answer':response.content,
    }

class ToolResultVerification(BaseModel):
    sufficient: bool = Field(
        description="工具调用结果是否足以回答用户问题"
    )
    need_more_tools: bool = Field(
        description="是否需要继续调用工具"
    )
    reason: str = Field(
        description="判断原因"
    )

async def check_tool_result(state:State):
    llm_with_structured=llm.with_structured_output(ToolResultVerification,include_raw=True)
    messages=TOOL_VERIFY_PROMPT.format_messages(
            chat_history=format_history(state),
            question=state.get('question'),
            tool_result=state.get('tool_result')
    )
    response:ToolResultVerification=(await llm_invoke_gateway(llm_with_structured,messages))['parsed']
    return {
        'need_more_tools':response.need_more_tools
    }
