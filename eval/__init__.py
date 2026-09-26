import ast
with open('./questions.jsonl', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    for line in lines:
        data = ast.literal_eval(line)
        if data['source_types'] == ['github']:
            print(data)


