import parser
data = parser.parse_workbook('temp_upload.xlsx')

for div_key, div in data.divisions.items():
    b_hours = {b.batch_id: 0 for b in div.batches}
    
    for s in data.sessions:
        if s.division_key == div_key and s.subject_type.value in ('PRACTICAL', 'TUTORIAL') and s.course_type == 'C' and s.batch_id != 'ALL':
            b_hours[s.batch_id] = b_hours.get(s.batch_id, 0) + s.duration
            
    print(f'{div_key} core batch sessions: {b_hours}')
