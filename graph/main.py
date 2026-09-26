import asyncio

from langgraph.constants import START, END
from langgraph.graph import StateGraph

from graph.node import classify_query, enterprise_retrieve, direct_generate_answer, rerank, generate_by_rag, \
    rewrite_query_standalone, rewrite_query_retry, persist_conversation, verify_answer, tool_use, generate_by_tool, \
    check_tool_result
from graph.routes import classify_query_route, route_after_relevance, route_after_verify, route_after_check_tool_result
from graph.state import State


async def _skip_persist(state: State):
    # 离线批量评估会跑几百轮问答，写进 messages 表只会污染真实会话历史
    return {}


def build_graph(persist: bool = True):
    graph = StateGraph(state_schema=State)
    graph.add_node('classify_query', classify_query)
    graph.add_node('rewrite_query_standalone', rewrite_query_standalone)
    graph.add_node('enterprise_retrieve', enterprise_retrieve)
    graph.add_node('direct_generate_answer', direct_generate_answer)
    graph.add_node('rerank', rerank)
    graph.add_node('generate_by_rag', generate_by_rag)
    graph.add_node('rewrite_query_retry', rewrite_query_retry)
    graph.add_node('verify_answer', verify_answer)
    graph.add_node('persist_conversation', persist_conversation if persist else _skip_persist)
    graph.add_node("tool_use",tool_use)
    graph.add_node('generate_by_tool',generate_by_tool)
    graph.add_node('check_tool_result',check_tool_result)

    graph.add_edge('generate_by_tool', 'persist_conversation')
    graph.add_edge('rewrite_query_standalone', 'enterprise_retrieve')
    graph.add_edge('rewrite_query_retry', 'enterprise_retrieve')
    graph.add_edge('enterprise_retrieve', 'rerank')
    graph.add_edge(START, 'classify_query')
    graph.add_edge('persist_conversation', END)
    graph.add_edge('direct_generate_answer', 'persist_conversation')
    graph.add_edge('tool_use', 'check_tool_result')
    graph.add_conditional_edges(
        'classify_query',
        classify_query_route,
        {
            'direct_answer_path': 'direct_generate_answer',
            'direct_retrieval_path': 'enterprise_retrieve',
            'rewrite_path': 'rewrite_query_standalone',
            'tool_path': 'tool_use',
        }
    )
    graph.add_conditional_edges(
        'rerank',
        route_after_relevance,
        {'rewrite_path': 'rewrite_query_retry', 'generate_answer_path': 'generate_by_rag'}
    )
    graph.add_edge('generate_by_rag', 'verify_answer')
    graph.add_conditional_edges(
        'verify_answer',
        route_after_verify,
        {'rewrite_path': 'rewrite_query_retry', 'persist_path': 'persist_conversation'}
    )
    graph.add_conditional_edges(
        'check_tool_result',
        route_after_check_tool_result,
        {'tool_path':'tool_use','generate_answer_path':'generate_by_tool'})


    return graph


builder = build_graph()


def get_app():
    return builder.compile()


async def main():
    app = get_app()
    result = await app.ainvoke(
        {
            # 'question':"介绍一下你自己",
            'question': "We saw quality regressions after enabling an INT8/mixed-precision variant and suspect KV-cache rounding on long-context requests. What request header can we set as a quick mitigation to bypass the KV cache, and in the dynamic quant handoff policy for the interactive-chat route what minimum KV-cache hit-rate and max sequence length are required before routing to a quant variant?",
            'thread_id': '4a283750-00be-49e7-99af-b5650963e8e8',
            'chat_history': []
        })
    return result


if __name__ == '__main__':
    result = asyncio.run(main())
    print(result)
    print("original_question:", result.get("original_question"))
    print("question:", result.get("question"))
    print("final_answer:", result.get("final_answer"))
    print("thread_id:", result.get("thread_id"))
    print("relevant_docs:", result.get("relevant_docs"))
    print("rag_context:", result.get("rag_context"))
    print("sources:", result.get("sources"))
    print("need_rewrite:", result.get("need_rewrite"))
    print("need_tool:", result.get("need_tool"))
    print("need_retrieval:", result.get("need_retrieval"))
    print("tool_result:", result.get("tool_result"))
