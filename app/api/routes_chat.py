from fastapi import APIRouter, HTTPException
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from loguru import logger

from db.messages_repository import get_messages, create_thread, rename_thread
from app.api.schema import ChatRequest, ChatResponse
from core.llm import get_deepseek_chat_llm, llm_invoke_gateway
from graph.main import get_app
from core.prompts import init_thread_name_prompt

router=APIRouter(tags=["chat"])

async def init_thread_name(thread_id:str,q:str,a:str):
    messages=init_thread_name_prompt.format_prompt(question=q,answer=a)
    llm=get_deepseek_chat_llm()
    response=await llm_invoke_gateway(llm,messages)

    new_title=response.content
    await rename_thread(thread_id,new_title)
    logger.info("会话标题已生成 title={}", new_title)
    return new_title



@router.post('/chat',response_model=ChatResponse)
async def chat(request:ChatRequest):
    thread_id = request.thread_id
    question = request.question
    app=get_app()
    is_new_thread=False
    if thread_id:
        history=await get_messages(thread_id)
    else:
        thread_id=await create_thread('新的聊天')
        history=[]
        is_new_thread = True
    # thread_id 绑一次即可：loguru 基于 contextvars，图节点、检索、MCP 工具链路
    # 都在复制了当前上下文的 task 里跑，它们打的日志会自动带上这个字段。
    with logger.contextualize(thread_id=thread_id):
        try:
            result=await app.ainvoke(
                {
                'question':question,
                'thread_id':thread_id,
                'chat_history':history
                }
            )
        except Exception as exc:
            logger.exception("问答图执行失败，返回 500")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if is_new_thread:
            await init_thread_name(thread_id, question, result.get('final_answer'))



    return ChatResponse(
        question=result.get('question'),
        answer=result.get('final_answer'),
        rag_context=result.get('rag_context',''),
        verification=result.get('verification',None),
        sources=result.get('sources',[]),
        thread_id=thread_id,
    )





