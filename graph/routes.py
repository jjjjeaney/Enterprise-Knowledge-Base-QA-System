from graph.node import MAX_REWRITE_TIMES, MAX_TOOL_USE_TIMES
from graph.state import State
from loguru  import logger

def classify_query_route(state:State):
    need_rewrite=state['need_rewrite']
    need_retrieval:bool=state['need_retrieval']
    need_tool=state['need_tool']
    logger.debug(
        "查询路由判定 need_rewrite={} need_retrieval={} need_tool={}",
        need_rewrite,
        need_retrieval,
        need_tool,
    )
    if need_retrieval:
            if need_rewrite:
                return 'rewrite_path'
            else:
                return 'direct_retrieval_path'
    else:
            if need_tool:
                return 'tool_path'
            else:
                return 'direct_answer_path'


def route_after_relevance(state:State):
    relevant_docs=state['relevant_docs']
    if not relevant_docs and state.get('rewrite_times',0)<MAX_REWRITE_TIMES:
        return 'rewrite_path'
    else:
        return 'generate_answer_path'

def route_after_verify(state:State):
    verification=state['verification']
    if not verification and state.get('rewrite_times',0)<MAX_REWRITE_TIMES:
        logger.warning(
            "答案未通过验证，进入重写 rewrite_times={}/{}",
            state.get('rewrite_times',0) + 1,
            MAX_REWRITE_TIMES,
        )
        return 'rewrite_path'
    else:
        if(verification):
            logger.info("答案通过验证")
        else:
            logger.warning(
                "答案未通过验证但重写次数已耗尽，仍然保存 max_rewrite_times={}",
                MAX_REWRITE_TIMES,
            )
        return 'persist_path'

def route_after_check_tool_result(state:State):

    if state.get('need_more_tools',False)==True:
        if state.get('tool_use_times',0)<MAX_TOOL_USE_TIMES:
            logger.info(
                "工具结果不足以回答，继续调用工具 tool_use_times={}/{}",
                state.get('tool_use_times',0),
                MAX_TOOL_USE_TIMES,
            )
            return 'tool_path'
        else:
            logger.warning(
                "工具调用次数已耗尽，强制生成结果 max_tool_use_times={}", MAX_TOOL_USE_TIMES
            )
            return 'generate_answer_path'
    else:
        return 'generate_answer_path'