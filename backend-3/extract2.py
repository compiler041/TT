import json

log_file = r'C:\Users\Vaibhav Rathod\.gemini\antigravity\brain\27c62125-25a9-4efb-8d16-7cce4063ba68\.system_generated\logs\transcript_full.jsonl'
with open('dump2.txt', 'w', encoding='utf-8') as out:
    for line in open(log_file, encoding='utf-8'):
        try:
            d = json.loads(line)
            if d.get('type') == 'USER_INPUT':
                out.write(f"STEP {d.get('step_index')}: {d.get('content')[:100]}\n")
            
            if 'tool_calls' in d:
                for tc in d['tool_calls']:
                    if tc.get('name') == 'default_api:replace_file_content':
                        args = tc.get('arguments', {})
                        if 'constraints.py' in args.get('TargetFile', ''):
                            out.write(f"STEP {d.get('step_index')} REPLACE FILE constraints.py\n")
        except:
            pass
