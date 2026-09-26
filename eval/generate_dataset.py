import ast
import asyncio
import json
from graph.main import get_app

question_path="./test_cases.jsonl"
rag_result_path= "6th_eval/rag_result.jsonl"


async def main():
    app = get_app()
    output = []

    with open(
        question_path,
        "r",
        encoding="utf-8"
    ) as f:

        for i,line in enumerate(f):
            try:
                case = ast.literal_eval(line)

                print(f"正在测试: {case['question_id']}")

                result = await app.ainvoke({
                    "question": case["question"],
                    "thread_id": 'fce5c5b1-5f46-4655-a174-23c0142c6dcd',
                    "chat_history": []
                })

                record = {
                    # Ground Truth
                    "question_id": case["question_id"],
                    "question_type": case["question_type"],
                    "source_types": case["source_types"],
                    "question": case["question"],
                    "gold_answer": case["gold_answer"],
                    "answer_facts": case["answer_facts"],

                    "retrieved_contexts": [
                        doc.get('content','')
                        for doc in result.get('relevant_docs',[])
                    ],
                    "response": result["final_answer"],
                    "sources": result["sources"],
                }

                output.append(record)
            except Exception as e:
                print(f"第{i}行出错：{e}")

    with open(
        rag_result_path,
        "w",
        encoding="utf-8"
    ) as f:

        for record in output:
            f.write(
                json.dumps(
                    record,
                    ensure_ascii=False
                ) + "\n"
            )


if __name__ == "__main__":
    asyncio.run(main())