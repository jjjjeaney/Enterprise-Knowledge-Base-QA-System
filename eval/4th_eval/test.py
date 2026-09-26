import ast

with open('./eval_result.jsonl', 'r', encoding='utf-8') as f:
    for i,line in enumerate(f):
        print(i)
        data = ast.literal_eval(line)
        print(f"{data['question_id']}   context_precision:{data['context_precision']},context_recall:{data['context_recall']},faithfulness:{data['faithfulness']},answer_relevancy:{data['answer_relevancy']}")
        print("\n")