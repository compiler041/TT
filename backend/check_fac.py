import parser
data = parser.parse_workbook('temp_upload.xlsx')

fac_hours = {f: 0 for f in data.faculty.keys()}
seen = set()

for s in data.sessions:
    if s.fac_id:
        # Same class key shouldn't overlap, meaning it is taught together
        if s.elective_group:
            cls_key = (s.elective_group, s.session_index)
        else:
            cls_key = (s.course_code, s.subject_type, s.session_index, s.division_key, s.batch_id)
        
        # wait, if a faculty teaches two batches at the same time, it's deduplicated.
        # But if they teach two batches at different times, it adds up!
        # wait, faculty NoOverlap deduplicates by (cls_key) without batch? 
        # I changed it to include batch! So they don't overlap, so they add up!
        # So cls_key MUST match Faculty NoOverlap logic
        if s.elective_group:
            k = (s.elective_group, s.session_index)
        else:
            k = (s.course_code, s.subject_type, s.session_index, s.division_key, s.batch_id)
            
        full_key = (s.fac_id, k)
        if full_key not in seen:
            seen.add(full_key)
            fac_hours[s.fac_id] += s.duration

for f, h in fac_hours.items():
    if h > 40:
        print(f"INFEASIBLE: {f} requires {h} hours > 40!")
    elif h > 20:
        print(f"WARN: {f} requires {h} hours")
