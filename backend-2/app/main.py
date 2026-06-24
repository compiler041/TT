"""
FastAPI backend for the Timetable (TT) Maker.

Endpoints:
  POST /upload            -> upload the combined xlsx (BASE SHEET, ROOM DETAILS,
                              MDM COURSES, FACULTY NA sheets) OR separate CSVs.
  GET  /data/summary       -> quick summary of loaded data
  POST /generate           -> run the CP-SAT solver and return the timetable
  GET  /timetable/{division}  -> filter generated timetable for one division
  GET  /timetable/export.csv  -> export full generated timetable as CSV
"""
from __future__ import annotations
import io
from typing import Optional

import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .data_loader import (
    load_all_from_xlsx,
    load_course_master,
    load_room_details,
    load_fixed_slots,
    load_faculty_busy,
)
from .engine import TimetableEngine
from .models import GenerationRequest, GenerationResult

app = FastAPI(title="TT Maker API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store (single-session use; swap for a DB/session store for multi-user)
STATE: dict = {
    "course_master": None,
    "rooms": None,
    "fixed_slots": None,
    "faculty_busy": None,
    "last_result": None,
}


@app.get("/")
def root():
    return {"status": "ok", "service": "TT Maker API"}


# ---------------------------------------------------------------------------
# Upload: single combined workbook
# ---------------------------------------------------------------------------
@app.post("/upload")
async def upload_workbook(file: UploadFile = File(...)):
    """
    Upload the combined Excel workbook containing sheets:
      BASE SHEET, ROOM DETAILS, MDM COURSES, FACULTY NA
    """
    content = await file.read()
    try:
        data = load_all_from_xlsx(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse workbook: {e}")

    STATE["course_master"] = data["course_master"]
    STATE["rooms"] = data["rooms"]
    STATE["fixed_slots"] = data["fixed_slots"]
    STATE["faculty_busy"] = data["faculty_busy"]
    STATE["last_result"] = None

    return {
        "status": "ok",
        "rows": {
            "course_master": len(data["course_master"]),
            "rooms": len(data["rooms"]),
            "fixed_slots": len(data["fixed_slots"]),
            "faculty_busy": len(data["faculty_busy"]),
        },
    }


# ---------------------------------------------------------------------------
# Upload: individual CSVs (CSV1/CSV2/CSV3 + room details)
# ---------------------------------------------------------------------------
@app.post("/upload/csv")
async def upload_csvs(
    course_master: UploadFile = File(..., description="CSV1: YEAR & SUBJECT (BASE SHEET columns)"),
    rooms: UploadFile = File(..., description="ROOM DETAILS csv (ROOM, TYPE)"),
    fixed_slots: Optional[UploadFile] = File(None, description="CSV3: MDM COURSES (fixed time slots)"),
    faculty_busy: Optional[UploadFile] = File(None, description="CSV2: FACULTY occupied slots"),
):
    cm_bytes = await course_master.read()
    rm_bytes = await rooms.read()

    try:
        cm_df = load_course_master(io.BytesIO(cm_bytes))
    except Exception:
        cm_df = pd.read_csv(io.BytesIO(cm_bytes))
        cm_df.columns = [str(c).strip().upper() for c in cm_df.columns]

    try:
        rm_df = load_room_details(io.BytesIO(rm_bytes))
    except Exception:
        rm_df = pd.read_csv(io.BytesIO(rm_bytes))
        rm_df.columns = [str(c).strip().upper() for c in rm_df.columns]

    fs_df = pd.DataFrame(columns=["CLASS", "TYPE OF COURSE", "SEM", "DAY", "SLOT"])
    if fixed_slots is not None:
        fs_bytes = await fixed_slots.read()
        try:
            fs_df = load_fixed_slots(io.BytesIO(fs_bytes))
        except Exception:
            pass

    fb_df = pd.DataFrame(columns=["FACULTY NAME", "DAY", "SLOT"])
    if faculty_busy is not None:
        fb_bytes = await faculty_busy.read()
        try:
            fb_df = load_faculty_busy(io.BytesIO(fb_bytes))
        except Exception:
            pass

    # Ensure required columns exist for course master loaded via plain pd.read_csv
    required = ["CLASS", "SEM", "BRANCH", "DIVISION", "NO OF BATCH", "TYPE OF COURSE",
                 "COURSE_NAME", "FACULTY NAME", "ROOM NO", "TYPE OF SUBJECT", "NO.OF HOURS"]
    for col in required:
        if col not in cm_df.columns:
            cm_df[col] = None
    if "FACULTY_LIST" not in cm_df.columns:
        cm_df["FACULTY_LIST"] = cm_df["FACULTY NAME"].apply(
            lambda x: [f.strip().upper() for f in str(x).split(",") if f.strip()]
            if pd.notna(x) and str(x).strip().lower() != "nan" else []
        )

    STATE["course_master"] = cm_df
    STATE["rooms"] = rm_df
    STATE["fixed_slots"] = fs_df
    STATE["faculty_busy"] = fb_df
    STATE["last_result"] = None

    return {
        "status": "ok",
        "rows": {
            "course_master": len(cm_df),
            "rooms": len(rm_df),
            "fixed_slots": len(fs_df),
            "faculty_busy": len(fb_df),
        },
    }


# ---------------------------------------------------------------------------
# Data summary
# ---------------------------------------------------------------------------
@app.get("/data/summary")
def data_summary():
    cm = STATE["course_master"]
    if cm is None:
        raise HTTPException(status_code=400, detail="No data uploaded yet. Call /upload first.")

    divisions = (
        cm[["CLASS", "SEM", "BRANCH", "DIVISION"]]
        .drop_duplicates()
        .to_dict(orient="records")
    )
    courses_per_division = (
        cm.groupby(["CLASS", "SEM", "BRANCH", "DIVISION"])["COURSE_NAME"]
        .nunique()
        .reset_index()
        .to_dict(orient="records")
    )
    return {
        "divisions": divisions,
        "courses_per_division": courses_per_division,
        "rooms": {
            "CR": STATE["rooms"][STATE["rooms"]["TYPE"] == "CR"]["ROOM"].tolist(),
            "LAB": STATE["rooms"][STATE["rooms"]["TYPE"] == "LAB"]["ROOM"].tolist(),
        },
    }


# ---------------------------------------------------------------------------
# Generate timetable
# ---------------------------------------------------------------------------
@app.post("/generate", response_model=GenerationResult)
def generate(req: GenerationRequest, time_limit_seconds: int = Query(60, ge=5, le=300)):
    cm = STATE["course_master"]
    if cm is None:
        raise HTTPException(status_code=400, detail="No data uploaded yet. Call /upload first.")

    engine = TimetableEngine(
        course_master=cm,
        rooms=STATE["rooms"],
        fixed_slots=STATE["fixed_slots"],
        faculty_busy=STATE["faculty_busy"],
        days=req.days,
        classes_filter=req.classes,
        branches_filter=req.branches,
    )

    result = engine.generate(time_limit_seconds=time_limit_seconds)
    STATE["last_result"] = result
    return GenerationResult(**result)


# ---------------------------------------------------------------------------
# Retrieve / filter generated timetable
# ---------------------------------------------------------------------------
@app.get("/timetable", response_model=GenerationResult)
def get_timetable():
    if STATE["last_result"] is None:
        raise HTTPException(status_code=400, detail="No timetable generated yet. Call /generate first.")
    return GenerationResult(**STATE["last_result"])


@app.get("/timetable/division")
def get_timetable_for_division(
    class_: str = Query(..., alias="class"),
    sem: float = Query(...),
    branch: str = Query(...),
    division: str = Query(...),
):
    if STATE["last_result"] is None:
        raise HTTPException(status_code=400, detail="No timetable generated yet. Call /generate first.")

    entries = [
        e for e in STATE["last_result"]["timetable"]
        if e["class_"] == class_.upper()
        and e["sem"] == sem
        and e["branch"] == branch.upper()
        and e["division"] == division.upper()
    ]
    return {"count": len(entries), "entries": entries}


@app.get("/timetable/export.csv")
def export_csv():
    if STATE["last_result"] is None:
        raise HTTPException(status_code=400, detail="No timetable generated yet. Call /generate first.")

    df = pd.DataFrame(STATE["last_result"]["timetable"])
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=timetable.csv"},
    )
