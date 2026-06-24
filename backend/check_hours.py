import parser
data = parser.parse_workbook('temp_upload.xlsx')

for div_key, div in data.divisions.items():
    batch_hours = {b.batch_id: 0 for b in div.batches}
    
    for batch in batch_hours.keys():
        seen_classes = set()
        total = 0
        for s in data.sessions:
            if s.division_key == div_key and s.batch_id in (batch, 'ALL'):
                if s.elective_group:
                    cls_key = (s.elective_group, s.session_index)
                else:
                    cls_key = (s.course_code, s.subject_type, s.session_index)
                if cls_key not in seen_classes:
                    seen_classes.add(cls_key)
                    total += s.duration
        
        if total > 40:
            print(f"INFEASIBLE: {div_key} {batch} requires {total} hours > 40 available slots!")
        else:
            print(f"OK: {div_key} {batch} requires {total} hours.")
