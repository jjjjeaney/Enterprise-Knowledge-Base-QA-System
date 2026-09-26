import json
import os
import sys
from datasets import Dataset
from dotenv import load_dotenv
from loguru import logger
from ragas.embeddings.base import embedding_factory
load_dotenv()
from ragas.metrics.collections import (
    ContextPrecision,
    ContextRecall,
    Faithfulness,
    AnswerRelevancy,
)
from openai import AsyncOpenAI
from ragas.llms import llm_factory

rag_result_path= "6th_eval/rag_result.jsonl"
eval_result_path= "6th_eval/eval_result.jsonl"

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<level>{message}</level>"
)


def setup_logger():
    """日志同时打到终端和结果目录下的 eval.log。

    终端滚掉之后还能回头 grep 出错的是哪一行、异常是什么；
    文件那份记到 DEBUG，包含每条回退细节。
    """
    logger.remove()
    logger.add(sys.stderr, format=LOG_FORMAT, level="INFO")
    log_dir = os.path.dirname(eval_result_path) or "."
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "eval.log")
    logger.add(log_path, format=LOG_FORMAT, level="DEBUG", encoding="utf-8")
    return log_path


def reset_eval_result():
    """开跑前清空上一次的结果，同目录重跑不会叠出重复行。"""
    os.makedirs(os.path.dirname(eval_result_path) or ".", exist_ok=True)
    open(eval_result_path, "w", encoding="utf-8").close()


def write_eval_result(eval_result: dict):
    """追加一条结果。逐行落盘，中途失败也能保住已经跑完的部分。"""
    os.makedirs(os.path.dirname(eval_result_path) or ".", exist_ok=True)
    with open(eval_result_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(eval_result, ensure_ascii=False) + "\n")


def fmt(value) -> str:
    if value is None:
        return "—"
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def _score_value(score):
    """取 ascore 返回值的数值分；备选模型也失败时 score_with_fallback 返回 -1.0。"""
    return getattr(score, "value", score)



dataset_dict = {
    "user_input": [],
    "response": [],
    "retrieved_contexts": [],
    "reference": [],
    'gold_answer': [],
    'question_id': [],
    'sources':[]
}

with open(rag_result_path, "r", encoding="utf-8") as f:
    for line in f:
        data = json.loads(line)

        dataset_dict["user_input"].append(data["question"])
        dataset_dict["response"].append(data["response"])
        dataset_dict["retrieved_contexts"].append(data.get('retrieved_contexts'))
        dataset_dict["gold_answer"].append(data["gold_answer"])
        dataset_dict["reference"].append('\n'.join(data.get('answer_facts',[])))
        dataset_dict["question_id"].append(data['question_id'])
        dataset_dict['sources'].append(data['sources'])
dataset = Dataset.from_dict(dataset_dict)



client_deepseek = AsyncOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL"), )
client_openai = AsyncOpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url=os.getenv("OPENROUTER_BASE_URL")
)
ragas_llm_deepseek = llm_factory( "deepseek-v4-pro", client=client_deepseek, )
ragas_llm_gpt=llm_factory( "gpt-5.4-mini", client=client_openai, )



embeddings = embedding_factory(
    "openai",
    model="text-embedding-3-large",
    client=client_openai,
    interface="modern", )


context_precision_deepseek = ContextPrecision(llm=ragas_llm_deepseek)
context_precision_gpt=ContextPrecision(llm=ragas_llm_gpt)

context_recall_deepseek = ContextRecall(llm=ragas_llm_deepseek)
context_recall_gpt=ContextRecall(llm=ragas_llm_gpt)

faithfulness = Faithfulness(llm=ragas_llm_deepseek)


faithfulness_deepseek = Faithfulness(llm=ragas_llm_deepseek)
faithfulness_gpt = Faithfulness(llm=ragas_llm_gpt)

answer_relevancy_deepseek = AnswerRelevancy(
    llm=ragas_llm_deepseek,
    embeddings=embeddings,
)
answer_relevancy_gpt = AnswerRelevancy(
    llm=ragas_llm_gpt,
    embeddings=embeddings,
)


async def score_with_fallback(metric_name, pos, qid, primary, backup, **kwargs):
    """先用 DeepSeek 判分，失败就退回 GPT；两边都失败则分值记为 -1.0，不再抛错。

    每次回退都带上 [第几行/共几行] 和 question_id，方便定位。
    异常消息要打 repr 而不是 str —— instructor 的校验失败经常是空消息，
    直接 f"{e}" 打出来就是一句没头没尾的 "failed:"。
    """
    try:
        result = await primary.ascore(**kwargs)
        logger.debug(f"{pos} {qid} {metric_name}: 主判分模型成功")
        return result
    except Exception as exc:
        logger.warning(
            f"{pos} {qid} {metric_name}: 主判分模型失败，回退备用模型 | {exc!r}"
        )

    try:
        result = await backup.ascore(**kwargs)
        logger.debug(f"{pos} {qid} {metric_name}: 备用判分模型成功")
        return result
    except Exception as exc:
        logger.error(
            f"{pos} {qid} {metric_name}: 备用判分模型也失败，分值记为 -1 | {exc!r}"
        )
        return -1.0


async def evaluate_row(row, pos):
    """算一道题的四项指标。pos 形如 [3/22]，只用来在日志里定位。"""
    qid = row["question_id"]

    if row['retrieved_contexts']==[]:
        # 没检索到上下文：CP/CR 记 0，Faithfulness/AnswerRelevancy 没有依据就不写
        # 这两个键。刻意不写 null —— 下游 2nd_eval/test.py 那批脚本用
        # ast.literal_eval 读，遇到 null 会直接抛 ValueError。
        # 这一行同样要落盘，否则汇总时分母和题目对不上。
        logger.info(f"{pos} {qid}: 没有检索到上下文，CP/CR 记 0，跳过另两项")
        return {

                "question_id": row["question_id"],
                "context_precision": 0,
                "context_recall": 0,
                'question': row["user_input"],
                'response': row["response"],
                'gold_answer': row["gold_answer"],
                'reference': row["reference"],
                'retrieved_contexts': row["retrieved_contexts"],
                'sources': row["sources"]
            }

    precision = await score_with_fallback(
        "ContextPrecision", pos, qid,
        context_precision_deepseek, context_precision_gpt,
        user_input=row["user_input"],
        retrieved_contexts=row["retrieved_contexts"],
        reference=row["reference"],
    )
    recall = await score_with_fallback(
        "ContextRecall", pos, qid,
        context_recall_deepseek, context_recall_gpt,
        user_input=row["user_input"],
        retrieved_contexts=row["retrieved_contexts"],
        reference=row["reference"],
    )
    faithfulness_score = await score_with_fallback(
        "Faithfulness", pos, qid,
        faithfulness_deepseek, faithfulness_gpt,
        user_input=row["user_input"],
        retrieved_contexts=row["retrieved_contexts"],
        response=row["response"],
    )
    answer_relevancy_score = await score_with_fallback(
        "AnswerRelevancy", pos, qid,
        answer_relevancy_deepseek, answer_relevancy_gpt,
        user_input=row["user_input"],
        response=row["response"],
    )

    return {
            "question_id": row["question_id"],
            "context_precision": _score_value(precision),
            "context_recall": _score_value(recall),
            'faithfulness': _score_value(faithfulness_score),
            'answer_relevancy': _score_value(answer_relevancy_score),
            'question': row["user_input"],
            'response': row["response"],
            'gold_answer': row["gold_answer"],
            'reference': row["reference"],
            'retrieved_contexts': row["retrieved_contexts"],
            'sources': row["sources"]
        }


async def evaluate_dataset():
    reset_eval_result()
    total = len(dataset)
    logger.info(f"共 {total} 题 | 输入 {rag_result_path} | 结果写入 {eval_result_path}")

    for i, row in enumerate(dataset):
        pos = f"[{i + 1}/{total}]"
        qid = row["question_id"]
        logger.info(f"{pos} {qid}: 开始评估")

        try:
            eval_result = await evaluate_row(row, pos)
        except Exception:
            # 把行号和题目跟完整堆栈一起记下来再往外抛，
            # 终端滚掉了也能去 eval.log 里查到断在哪一行
            logger.opt(exception=True).error(
                f"{pos} {qid}: 评估失败，运行到此中断"
            )
            raise

        write_eval_result(eval_result)
        logger.info(
            f"{pos} {qid}: 完成 | "
            f"cp={fmt(eval_result.get('context_precision'))} "
            f"cr={fmt(eval_result.get('context_recall'))} "
            f"faith={fmt(eval_result.get('faithfulness'))} "
            f"ar={fmt(eval_result.get('answer_relevancy'))}"
        )

    logger.info(f"全部 {total} 题完成，结果已写入 {eval_result_path}")


if __name__ == "__main__":
    import asyncio
    log_path = setup_logger()
    logger.info(f"日志同时写入 {log_path}")
    asyncio.run(evaluate_dataset())
