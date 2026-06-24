import parser
data = parser.parse_workbook('temp_upload.xlsx')
for s in data.sessions:
    if s.division_key == 'LY_EXCP_A' and s.subject_type.value == 'PRACTICAL':
        print(f'{s.course_code} {s.batch_id} dur={s.duration} fac={s.fac_id}')
