import parser
data = parser.parse_workbook('temp_upload.xlsx')

room_hours = {r: 0 for r in data.rooms.keys()}
seen = set()

for s in data.sessions:
    if s.room_no:
        if s.elective_group:
            k = (s.elective_group, s.session_index)
        else:
            k = (s.course_code, s.subject_type, s.session_index, s.division_key, s.batch_id)
            
        full_key = (s.room_no, k)
        if full_key not in seen:
            seen.add(full_key)
            room_hours[s.room_no] = room_hours.get(s.room_no, 0) + s.duration

for r, h in room_hours.items():
    if h > 40:
        print(f"INFEASIBLE: Room {r} requires {h} hours > 40!")
    else:
        print(f"OK: Room {r} requires {h} hours.")
