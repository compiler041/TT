import json

log_file = r'C:\Users\Vaibhav Rathod\.gemini\antigravity\brain\27c62125-25a9-4efb-8d16-7cce4063ba68\.system_generated\logs\transcript_full.jsonl'
with open('dump.txt', 'w', encoding='utf-8') as out:
    for line in open(log_file, encoding='utf-8'):
        try:
            d = json.loads(line)
            if 'content' in d and 'def score_placement' in d['content']:
                out.write('STEP ' + str(d.get('step_index')) + '\n')
                out.write(d['content'][:1000] + '\n\n')
            elif 'tool_calls' in d:
                for tc in d['tool_calls']:
                    args = tc.get('arguments', {})
                    if 'ReplacementContent' in args and 'def score_placement' in args['ReplacementContent']:
                        out.write('STEP ' + str(d.get('step_index')) + ' TOOL CALL\n')
                        out.write(args['ReplacementContent'][:1000] + '\n\n')
        except:
            pass
