import json

log_file = r'C:\Users\Vaibhav Rathod\.gemini\antigravity\brain\27c62125-25a9-4efb-8d16-7cce4063ba68\.system_generated\logs\transcript_full.jsonl'
with open('dump3.txt', 'w', encoding='utf-8') as out:
    for line in open(log_file, encoding='utf-8'):
        try:
            d = json.loads(line)
            if d.get('type') == 'USER_INPUT' and d.get('step_index') in [522, 578, 589, 595]:
                out.write(f"STEP {d.get('step_index')} TIME: {d.get('content')[-100:]}\n")
        except:
            pass
