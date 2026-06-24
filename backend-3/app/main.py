"""
FastAPI entrypoint.

Routes:
  GET  /          -> upload form
  POST /generate  -> run scheduler, return HTML timetable grid

JSON API (used by the standalone frontend):
  POST /api/upload              -> parse Excel, return stats
  POST /api/solve               -> run scheduler, return status
  GET  /api/timetable           -> full timetable JSON (divisions, faculty list, room list)
  GET  /api/timetable/faculty/X -> faculty-specific grid
  GET  /api/timetable/room/X    -> room-specific grid
  GET  /api/conflicts           -> scheduling conflicts / unscheduled items
  GET  /api/export              -> download timetable as Excel
"""

import os
import time as _time
import tempfile
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.middleware.cors import CORSMiddleware

from app.loader import (
    load_subjects, load_classrooms, load_fixed_slots, load_faculty_unavailability,
    extract_division_meta, build_practical_sessions, build_tutorial_sessions
)
from app.scheduler import run_full_schedule
from app.models import Timetable, ScheduleEntry, VALID_DAYS, VALID_PERIODS, PERIOD_TIME_MAP

app = FastAPI(title="Timetable Scheduler")
templates = Jinja2Templates(directory="templates")

# ------------------------------------------------------------------
# CORS - allow the frontend dev server to call the API
# ------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------
# In-memory state shared by the JSON API endpoints
# ------------------------------------------------------------------
_state: dict = {
    "subjects": [],
    "classrooms": [],
    "fixed_slots": [],
    "faculty_unavailability": [],
    "timetable": None,       # Timetable object after solve
    "unscheduled": [],        # list[Subject] that couldn't be placed
    "bottlenecks": [],
    "division_meta": None,
    "practical_sessions": None,
    "tutorial_sessions": None,
    "solve_time_ms": 0,
    "tmp_path": None,         # temp file kept for export
}

# ====================================================================
# Original template-based routes (untouched)
# ====================================================================

@app.get("/", response_class=HTMLResponse)
async def upload_form(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@app.post("/generate", response_class=HTMLResponse)
async def generate_timetable(request: Request, file: UploadFile = File(...)):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Please upload an Excel file (.xlsx)")

    # Save upload to a temp file so openpyxl can read it
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        subjects   = load_subjects(tmp_path)
        classrooms = load_classrooms(tmp_path)
        faculty_unavailability = []

        try:
            fixed_slots = load_fixed_slots(tmp_path)
        except ValueError as e:
            fixed_slots = []
            print(f"[WARN] Fixed slots not loaded (fix TIME column): {e}")

        try:
            faculty_unavailability = load_faculty_unavailability(tmp_path)
        except ValueError as e:
            faculty_unavailability = []
            print(f"[WARN] Faculty unavailability not loaded (fix TIME column): {e}")

        division_meta = extract_division_meta(subjects)
        practical_sessions = build_practical_sessions(subjects, division_meta)
        tutorial_sessions = build_tutorial_sessions(subjects, division_meta)

        timetable, unscheduled, bottlenecks = run_full_schedule(
            subjects, classrooms, fixed_slots, faculty_unavailability,
            division_meta, practical_sessions, tutorial_sessions
        )

    finally:
        os.unlink(tmp_path)

    # Build grid data: {class_key -> {(day, period) -> ScheduleEntry}}
    grid_data = _build_grid(timetable)

    return templates.TemplateResponse("timetable.html", {
        "request": request,
        "grid_data": grid_data,
        "days": VALID_DAYS,
        "periods": list(PERIOD_TIME_MAP.keys()),
        "period_times": {p: f"{t[0].strftime('%H:%M')}-{t[1].strftime('%H:%M')}"
                         for p, t in PERIOD_TIME_MAP.items()},
        "unscheduled": unscheduled,
        "total": len(timetable.entries),
    })


def _build_grid(timetable: Timetable) -> dict:
    """
    Returns:
    {
      class_key (year, branch, division): {
        (day, period): [ScheduleEntry, ...]   # list because multiple batches can share a slot
      }
    }
    """
    grid: dict = {}
    year_div_to_branches = {}
    for entry in timetable.entries:
        y, b, d = entry.subject.class_key
        if b != "ALL":
            year_div_to_branches.setdefault((y, d), set()).add(b)

    for entry in timetable.entries:
        y, b, d = entry.subject.class_key
        if b == "ALL":
            targets = [(y, br, d) for br in year_div_to_branches.get((y, d), [])]
            if not targets:
                targets = [entry.subject.class_key]
        else:
            targets = [entry.subject.class_key]

        for key in targets:
            if key not in grid:
                grid[key] = {}
            for period in entry.timeslot.occupied_periods:
                slot_key = (entry.timeslot.day, period)
                grid[key].setdefault(slot_key, []).append(entry)
    return grid


# ====================================================================
# JSON API — used by the standalone frontend
# ====================================================================

DAY_INDEX = {d: i for i, d in enumerate(VALID_DAYS)}  # M->0, T->1 ...


def _entry_to_dict(entry: ScheduleEntry) -> dict:
    """Convert a ScheduleEntry to the JSON shape the frontend grid renderer expects."""
    subj = entry.subject
    day_idx = DAY_INDEX.get(entry.timeslot.day, 0)
    period = entry.timeslot.start_period
    # slot_id = day_index * 9 + (period - 1)  -- matches frontend's slotId formula
    # Frontend uses 9 rows (periods 1-9, with period 5 = lunch)
    # Map backend periods (1-9) to frontend rows (1-9, 5=lunch)
    # Backend: periods 1-4 are pre-lunch, 5-9 are post-lunch
    # Frontend: rows 1-4 pre-lunch, row 5 = lunch, rows 6-9 post-lunch
    # So backend period 5 -> frontend row 6, period 6 -> row 7, etc.
    if period <= 4:
        frontend_period = period
    else:
        frontend_period = period + 1  # shift by 1 to skip lunch row

    slot_id = day_idx * 9 + (frontend_period - 1)

    stype = "THEORY"
    if subj.subject_type == "P":
        stype = "PRACTICAL"
    elif subj.subject_type == "T":
        stype = "TUTORIAL"

    return {
        "slot_id": slot_id,
        "course_code": subj.name,
        "subject_type": stype,
        "faculty": [entry.faculty_code] if entry.faculty_code else [],
        "rooms": [entry.classroom.room_id],
        "batches": [entry.batch] if entry.batch != "ALL" else ["ALL"],
        "duration": subj.duration,
        "division": f"{subj.year}-{subj.branch}-{subj.division}",
    }


# ---------- POST /api/upload ----------

@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    if not file.filename.endswith((".xlsx", ".xls")):
        return JSONResponse({"success": False, "error": "Please upload an Excel file (.xlsx)"})

    # Clean up previous temp file if any
    if _state["tmp_path"] and os.path.exists(_state["tmp_path"]):
        try:
            os.unlink(_state["tmp_path"])
        except OSError:
            pass

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    _state["tmp_path"] = tmp_path

    try:
        subjects = load_subjects(tmp_path)
        classrooms = load_classrooms(tmp_path)
        fixed_slots = []
        faculty_unavailability = []

        try:
            fixed_slots = load_fixed_slots(tmp_path)
        except ValueError as e:
            print(f"[WARN] Fixed slots not loaded: {e}")

        try:
            faculty_unavailability = load_faculty_unavailability(tmp_path)
        except ValueError as e:
            print(f"[WARN] Faculty unavailability not loaded: {e}")

        division_meta = extract_division_meta(subjects)
        practical_sessions = build_practical_sessions(subjects, division_meta)
        tutorial_sessions = build_tutorial_sessions(subjects, division_meta)

        _state["subjects"] = subjects
        _state["classrooms"] = classrooms
        _state["fixed_slots"] = fixed_slots
        _state["faculty_unavailability"] = faculty_unavailability
        _state["division_meta"] = division_meta
        _state["practical_sessions"] = practical_sessions
        _state["tutorial_sessions"] = tutorial_sessions
        _state["timetable"] = None
        _state["unscheduled"] = []
        _state["bottlenecks"] = []

        # Compute stats
        divisions = set()
        courses = set()
        faculty = set()
        for s in subjects:
            divisions.add(f"{s.year}-{s.branch}-{s.division}")
            courses.add(s.name)
            for f in s.faculty_codes:
                faculty.add(f)

        rooms = set(c.room_id for c in classrooms)

        return JSONResponse({
            "success": True,
            "data": {
                "divisions": len(divisions),
                "courses": len(courses),
                "faculty": len(faculty),
                "rooms": len(rooms),
            }
        })

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ---------- POST /api/solve ----------

@app.post("/api/solve")
async def api_solve():
    if not _state["subjects"]:
        raise HTTPException(status_code=400, detail="No data uploaded yet. Upload an Excel file first.")

    t0 = _time.time()
    timetable, unscheduled, bottlenecks = run_full_schedule(
        _state["subjects"],
        _state["classrooms"],
        _state["fixed_slots"],
        _state["faculty_unavailability"],
        _state.get("division_meta"),
        _state.get("practical_sessions"),
        _state.get("tutorial_sessions")
    )
    elapsed_ms = int((_time.time() - t0) * 1000)

    _state["timetable"] = timetable
    _state["unscheduled"] = unscheduled
    _state["bottlenecks"] = bottlenecks
    _state["solve_time_ms"] = elapsed_ms

    status = "OPTIMAL" if len(unscheduled) == 0 else "FEASIBLE"

    return JSONResponse({
        "status": status,
        "solve_time_ms": elapsed_ms,
        "total_entries": len(timetable.entries),
        "unscheduled_count": len(unscheduled),
    })


# ---------- GET /api/timetable ----------

@app.get("/api/timetable")
async def api_timetable():
    timetable: Timetable = _state["timetable"]
    if timetable is None:
        raise HTTPException(status_code=400, detail="Timetable not generated yet.")

    # Build per-division grids
    division_map: dict[str, list[dict]] = {}
    all_faculty: set[str] = set()
    all_rooms: set[str] = set()

    year_div_to_branches = {}
    for entry in timetable.entries:
        y, b, d = entry.subject.class_key
        if b != "ALL":
            year_div_to_branches.setdefault((y, d), set()).add(b)

    for entry in timetable.entries:
        d = _entry_to_dict(entry)
        y, b, div = entry.subject.class_key
        
        if b == "ALL":
            targets = [f"{y}-{br}-{div}" for br in year_div_to_branches.get((y, div), [])]
            if not targets:
                targets = [d["division"]]
        else:
            targets = [d["division"]]

        for div_key in targets:
            d_copy = d.copy()
            d_copy["division"] = div_key
            division_map.setdefault(div_key, []).append(d_copy)

        if entry.faculty_code:
            all_faculty.add(entry.faculty_code)
        all_rooms.add(entry.classroom.room_id)

    division_list = sorted(division_map.keys())
    faculty_list = sorted(all_faculty)
    room_list = sorted(all_rooms)

    divisions = {}
    for div_key in division_list:
        divisions[div_key] = {"grid": division_map[div_key]}

    return JSONResponse({
        "division_list": division_list,
        "faculty_list": faculty_list,
        "room_list": room_list,
        "divisions": divisions,
    })


# ---------- GET /api/timetable/faculty/{fac_id} ----------

@app.get("/api/timetable/faculty/{fac_id}")
async def api_faculty_timetable(fac_id: str):
    timetable: Timetable = _state["timetable"]
    if timetable is None:
        raise HTTPException(status_code=400, detail="Timetable not generated yet.")

    entries = timetable.get_by_faculty(fac_id)
    grid = [_entry_to_dict(e) for e in entries]

    return JSONResponse({"faculty": fac_id, "grid": grid})


# ---------- GET /api/timetable/room/{room_no} ----------

@app.get("/api/timetable/room/{room_no}")
async def api_room_timetable(room_no: str):
    timetable: Timetable = _state["timetable"]
    if timetable is None:
        raise HTTPException(status_code=400, detail="Timetable not generated yet.")

    entries = [e for e in timetable.entries if e.classroom.room_id == room_no]
    grid = [_entry_to_dict(e) for e in entries]

    return JSONResponse({"room": room_no, "grid": grid})


# ---------- GET /api/conflicts ----------

@app.get("/api/conflicts")
async def api_conflicts():
    unscheduled = _state.get("unscheduled", [])
    bottlenecks = _state.get("bottlenecks", [])
    conflicts: dict[str, list[str]] = {}

    if unscheduled:
        msgs = []
        for s in unscheduled:
            fac = ", ".join(s.faculty_codes) if s.faculty_codes else "N/A"
            msgs.append(
                f"{s.name} ({s.year} {s.branch} {s.division}, "
                f"batch={s.batch}, type={s.subject_type}, faculty={fac})"
            )
        conflicts["Unscheduled Subjects"] = msgs
        
    if bottlenecks:
        msgs = []
        for b in bottlenecks:
            msg = f"{b['division']} - {b['session']} could not find a slot."
            if b['failures']:
                msg += f" Reasons: {', '.join(f[1] for f in b['failures'])}"
            msgs.append(msg)
        conflicts["Session Bottlenecks"] = msgs

    return JSONResponse({"conflicts": conflicts})


# ---------- GET /api/export ----------

@app.get("/api/export")
async def api_export():
    timetable: Timetable = _state["timetable"]
    if timetable is None:
        raise HTTPException(status_code=400, detail="Timetable not generated yet.")

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")

    wb = Workbook()
    wb.remove(wb.active)

    # Build per-division grids
    grid = _build_grid(timetable)

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    period_labels = []
    for p in sorted(PERIOD_TIME_MAP.keys()):
        t = PERIOD_TIME_MAP[p]
        period_labels.append((p, f"P{p} ({t[0].strftime('%H:%M')}-{t[1].strftime('%H:%M')})"))

    for class_key, day_period_map in grid.items():
        sheet_name = f"{class_key[0]}-{class_key[1]}-{class_key[2]}"[:31]
        ws = wb.create_sheet(title=sheet_name)

        # Header row
        ws.cell(row=1, column=1, value="Period").font = header_font
        ws.cell(row=1, column=1).fill = header_fill
        ws.cell(row=1, column=1).alignment = center_align
        ws.cell(row=1, column=1).border = thin_border

        for di, day in enumerate(VALID_DAYS):
            cell = ws.cell(row=1, column=di + 2, value=day)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border

        # Data rows
        for ri, (period, label) in enumerate(period_labels):
            row = ri + 2
            ws.cell(row=row, column=1, value=label).alignment = center_align
            ws.cell(row=row, column=1).border = thin_border
            ws.cell(row=row, column=1).font = Font(bold=True)

            for di, day in enumerate(VALID_DAYS):
                slot_key = (day, period)
                entries_at_slot = day_period_map.get(slot_key, [])
                if entries_at_slot:
                    lines = []
                    for e in entries_at_slot:
                        line = e.subject.name
                        if e.batch != "ALL":
                            line += f" (B{e.batch})"
                        line += f"\n{e.faculty_code} | {e.classroom.room_id}"
                        lines.append(line)
                    cell_value = "\n---\n".join(lines)
                else:
                    cell_value = ""

                cell = ws.cell(row=row, column=di + 2, value=cell_value)
                cell.alignment = center_align
                cell.border = thin_border

        # Auto-width
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 22

    # Save to temp file
    export_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    wb.save(export_path)

    return FileResponse(
        export_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="timetable_export.xlsx",
    )

@app.get("/api/debug_subjects")
def debug_subjects():
    all_faculty: set[str] = set()
    all_rooms: set[str] = set()

    year_div_to_branches = {}
    for entry in timetable.entries:
        y, b, d = entry.subject.class_key
        if b != "ALL":
            year_div_to_branches.setdefault((y, d), set()).add(b)

    for entry in timetable.entries:
        d = _entry_to_dict(entry)
        y, b, div = entry.subject.class_key
        
        if b == "ALL":
            targets = [f"{y}-{br}-{div}" for br in year_div_to_branches.get((y, div), [])]
            if not targets:
                targets = [d["division"]]
        else:
            targets = [d["division"]]

        for div_key in targets:
            d_copy = d.copy()
            d_copy["division"] = div_key
            division_map.setdefault(div_key, []).append(d_copy)

        if entry.faculty_code:
            all_faculty.add(entry.faculty_code)
        all_rooms.add(entry.classroom.room_id)

    division_list = sorted(division_map.keys())
    faculty_list = sorted(all_faculty)
    room_list = sorted(all_rooms)

    divisions = {}
    for div_key in division_list:
        divisions[div_key] = {"grid": division_map[div_key]}

    return JSONResponse({
        "division_list": division_list,
        "faculty_list": faculty_list,
        "room_list": room_list,
        "divisions": divisions,
    })


# ---------- GET /api/timetable/faculty/{fac_id} ----------

@app.get("/api/timetable/faculty/{fac_id}")
async def api_faculty_timetable(fac_id: str):
    timetable: Timetable = _state["timetable"]
    if timetable is None:
        raise HTTPException(status_code=400, detail="Timetable not generated yet.")

    entries = timetable.get_by_faculty(fac_id)
    grid = [_entry_to_dict(e) for e in entries]

    return JSONResponse({"faculty": fac_id, "grid": grid})


# ---------- GET /api/timetable/room/{room_no} ----------

@app.get("/api/timetable/room/{room_no}")
async def api_room_timetable(room_no: str):
    timetable: Timetable = _state["timetable"]
    if timetable is None:
        raise HTTPException(status_code=400, detail="Timetable not generated yet.")

    entries = [e for e in timetable.entries if e.classroom.room_id == room_no]
    grid = [_entry_to_dict(e) for e in entries]

    return JSONResponse({"room": room_no, "grid": grid})


# ---------- GET /api/conflicts ----------

@app.get("/api/conflicts")
async def api_conflicts():
    unscheduled = _state.get("unscheduled", [])
    bottlenecks = _state.get("bottlenecks", [])
    conflicts: dict[str, list[str]] = {}

    if unscheduled:
        msgs = []
        for s in unscheduled:
            fac = ", ".join(s.faculty_codes) if s.faculty_codes else "N/A"
            msgs.append(
                f"{s.name} ({s.year} {s.branch} {s.division}, "
                f"batch={s.batch}, type={s.subject_type}, faculty={fac})"
            )
        conflicts["Unscheduled Subjects"] = msgs
        
    if bottlenecks:
        msgs = []
        for b in bottlenecks:
            msg = f"{b['division']} - {b['session']} could not find a slot."
            if b['failures']:
                msg += f" Reasons: {', '.join(f[1] for f in b['failures'])}"
            msgs.append(msg)
        conflicts["Session Bottlenecks"] = msgs

    return JSONResponse({"conflicts": conflicts})


# ---------- GET /api/export ----------

@app.get("/api/export")
async def api_export():
    timetable: Timetable = _state["timetable"]
    if timetable is None:
        raise HTTPException(status_code=400, detail="Timetable not generated yet.")

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        raise HTTPException(status_code=500, detail="openpyxl not installed")

    wb = Workbook()
    wb.remove(wb.active)

    # Build per-division grids
    grid = _build_grid(timetable)

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    center_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    period_labels = []
    for p in sorted(PERIOD_TIME_MAP.keys()):
        t = PERIOD_TIME_MAP[p]
        period_labels.append((p, f"P{p} ({t[0].strftime('%H:%M')}-{t[1].strftime('%H:%M')})"))

    for class_key, day_period_map in grid.items():
        sheet_name = f"{class_key[0]}-{class_key[1]}-{class_key[2]}"[:31]
        ws = wb.create_sheet(title=sheet_name)

        # Header row
        ws.cell(row=1, column=1, value="Period").font = header_font
        ws.cell(row=1, column=1).fill = header_fill
        ws.cell(row=1, column=1).alignment = center_align
        ws.cell(row=1, column=1).border = thin_border

        for di, day in enumerate(VALID_DAYS):
            cell = ws.cell(row=1, column=di + 2, value=day)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center_align
            cell.border = thin_border

        # Data rows
        for ri, (period, label) in enumerate(period_labels):
            row = ri + 2
            ws.cell(row=row, column=1, value=label).alignment = center_align
            ws.cell(row=row, column=1).border = thin_border
            ws.cell(row=row, column=1).font = Font(bold=True)

            for di, day in enumerate(VALID_DAYS):
                slot_key = (day, period)
                entries_at_slot = day_period_map.get(slot_key, [])
                if entries_at_slot:
                    lines = []
                    for e in entries_at_slot:
                        line = e.subject.name
                        if e.batch != "ALL":
                            line += f" (B{e.batch})"
                        line += f"\n{e.faculty_code} | {e.classroom.room_id}"
                        lines.append(line)
                    cell_value = "\n---\n".join(lines)
                else:
                    cell_value = ""

                cell = ws.cell(row=row, column=di + 2, value=cell_value)
                cell.alignment = center_align
                cell.border = thin_border

        # Auto-width
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 22

    # Save to temp file
    export_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    wb.save(export_path)

    return FileResponse(
        export_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="timetable_export.xlsx",
    )

@app.get("/api/debug_subjects")
def debug_subjects():
    if not _state.get("subjects"):
        return {"error": "no state"}
    out = []
    for s in _state["subjects"]:
        if s.name in ["AIML", "CV", "MRAS", "AICL"]:
            out.append({
                "name": s.name,
                "branch": s.branch,
                "division": s.division,
                "course_type": s.course_type,
                "subject_type": s.subject_type,
                "room": s.room,
                "faculty_codes": s.faculty_codes,
                "hours_per_week": s.hours_per_week
            })
    return out
