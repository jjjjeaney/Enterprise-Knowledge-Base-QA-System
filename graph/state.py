from typing import TypedDict, Any, List, Dict


class State(TypedDict):
    question:str
    original_question:str
    need_rewrite:bool
    need_tool:bool

    # conversation / thread continuity
    thread_id: str
    chat_history: List[Dict[str, str]]  # [{"role": "user"|"assistant", "content": "..."}]


    # routing
    query_type: str
    need_retrieval: bool
    need_tools: bool
    verification: bool


    retrieval_docs: List[dict[str, Any]]   #[{'id':xx,'content':xx,'metadate':xx}]
    relevant_docs:List[dict[str, Any]]
    rag_context:str
    sources:List[str]

    need_more_tools: bool
    tool_result:str
    tool_use_times:int

    rewrite_times:int
    final_answer:str
