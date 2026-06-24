import parser
data = parser.parse_workbook('temp_upload.xlsx')

for div_key, div in data.divisions.items():
    b_hours = {b.batch_id: 0 for b in div.batches}
    
    for s in data.sessions:
        if s.division_key == div_key and s.subject_type.value == 'PRACTICAL' and s.course_type == 'C':
            b_hours[s.batch_id] = b_hours.get(s.batch_id, 0) + s.duration
            
    if len(set(b_hours.values())) > 1:
        print(f'{div_key} has unbalanced CORE practicals: {b_hours}')
    else:
        print(f'{div_key} is balanced for CORE practicals: {b_hours}')
