import json
import re

log_file = r'C:\Users\Vaibhav Rathod\.gemini\antigravity\brain\27c62125-25a9-4efb-8d16-7cce4063ba68\.system_generated\logs\transcript_full.jsonl'
with open('dump4.txt', 'w', encoding='utf-8') as out:
    for line in open(log_file, encoding='utf-8'):
        try:
            d = json.loads(line)
            if d.get('type') == 'USER_INPUT':
                content = d.get('content')
                m = re.search(r'The current local time is: (.+)', content)
                if m:
                    out.write(f"STEP {d.get('step_index')} TIME: {m.group(1)}\n")
        except:
            pass
