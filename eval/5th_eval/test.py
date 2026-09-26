import ast

with open('./eval_result.jsonl', 'r', encoding='utf-8') as f:
    for i,line in enumerate(f):
        print(i)
        data = ast.literal_eval(line)
        print(f"{data.get('question_id',0)}   context_precision:{data.get('context_precision',0)},context_recall:{data.get('context_recall',0)},faithfulness:{data.get('faithfulness')},answer_relevancy:{data.get('answer_relevancy')}")
        print("\n")