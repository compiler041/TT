import parser
data = parser.parse_workbook('temp_upload.xlsx')

for f in data.faculty.values():
    blocked = bin(f.blocked_mask).count('1')
    if blocked > 20:
        print(f"{f.fac_id} blocked slots: {blocked}")

for d_key, d in data.divisions.items():
    blocked = bin(d.blocked_mask).count('1')
    if blocked > 20:
        print(f"Division {d_key} blocked slots: {blocked}")
