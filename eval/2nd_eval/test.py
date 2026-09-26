import ast

with open('./eval_result.jsonl', 'r', encoding='utf-8') as f:
    for i,line in enumerate(f):
        print(i)
        data = ast.literal_eval(line)
        print(data['question_id'])
        print(data['context_precision'])
        print(data['context_recall'])
        print("\n")