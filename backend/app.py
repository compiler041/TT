"""
app.py — FastAPI backend
"""
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import shutil
import time
from fastapi.responses import Response

from parser import parse_workbook
from solver import solve_timetable
from exporter import export_timetable_to_excel
from models import slot_to_day_period

app = FastAPI(title="Timetable Generator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
class AppState:
    data = None
    timetable = None
    status = "IDLE"
    solve_time_ms = 0

state = AppState()

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    file_path = "temp_upload.xlsx"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        data = parse_workbook(file_path)
        state.data = data
        state.timetable = None
        state.status = "READY"
        
        return {
            "success": True,
            "data": {
                "divisions": len(data.divisions),
                "courses": len(data.courses),
                "faculty": len(data.faculty),
                "rooms": len(data.rooms),
                "sessions_count": len(data.sessions)
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/solve")
async def solve():
    if not state.data:
        return {"status": "ERROR", "message": "No data uploaded"}
        
    start_time = time.time()
    # Increase time to 120s for better solutions
    status, idx = solve_timetable(state.data, max_time_seconds=120)
    end_time = time.time()
    
    state.status = status
    state.solve_time_ms = int((end_time - start_time) * 1000)
    state.timetable = idx
        
    return {
        "status": status,
        "solve_time_ms": state.solve_time_ms
    }

def format_grid(records):
    # Returns a list of all occupied slots
    grid = []
    for r in records:
        day, period = slot_to_day_period(r.slot_id)
        grid.append({
            "slot_id": r.slot_id,
            "day": day,
            "period": period,
            "duration": r.duration,
            "course_code": r.course_code,
            "subject_type": r.subject_type.value,
            "faculty": r.fac_ids,
            "rooms": r.room_nos,
            "batches": r.batch_ids
        })
    return grid

@app.get("/api/timetable")
async def get_timetable():
    if not state.timetable:
        return {"error": "No timetable generated"}
        
    # Build divisions
    divisions_data = {}
    for div_key in state.data.divisions.keys():
        week_records = state.timetable.get_division_week(div_key)
        unique_recs = {}
        for rec_list in week_records.values():
            for r in rec_list:
                unique_recs[id(r)] = r
        divisions_data[div_key] = {
            "grid": format_grid(unique_recs.values())
        }
        
    faculty_list = sorted(list(state.data.faculty.keys()))
    room_list = sorted(list(state.data.rooms.keys()))
    div_list = sorted(list(state.data.divisions.keys()))
    
    return {
        "divisions": divisions_data,
        "faculty_list": faculty_list,
        "room_list": room_list,
        "division_list": div_list
    }

@app.get("/api/timetable/faculty/{fac_id}")
async def get_faculty_timetable(fac_id: str):
    if not state.timetable:
        return {"error": "No timetable generated"}
    week_records = state.timetable.get_faculty_week(fac_id)
    unique_recs = {}
    for rec_list in week_records.values():
        for r in rec_list:
            unique_recs[id(r)] = r
    return {"grid": format_grid(unique_recs.values())}

@app.get("/api/timetable/room/{room_no}")
async def get_room_timetable(room_no: str):
    if not state.timetable:
        return {"error": "No timetable generated"}
    week_records = state.timetable.get_room_week(room_no)
    unique_recs = {}
    for rec_list in week_records.values():
        for r in rec_list:
            unique_recs[id(r)] = r
    return {"grid": format_grid(unique_recs.values())}

@app.get("/api/conflicts")
async def get_conflicts():
    if not state.timetable:
        return {"conflicts": {}}
    return {"conflicts": state.timetable.conflict_log}

@app.get("/api/export")
async def export_excel():
    if not state.timetable or not state.data:
        return {"error": "No timetable generated"}
        
    excel_data = export_timetable_to_excel(state.data, state.timetable)
    
    return Response(
        content=excel_data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=timetable.xlsx"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
