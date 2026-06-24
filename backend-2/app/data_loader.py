"""
Loads and normalizes the three input files:
 - CSV1 (BASE SHEET): YEAR & SUBJECT master data (course/faculty/room/hours)
 - CSV2 (FACULTY NA): faculty occupied / not-available slots
 - CSV3 (MDM COURSES): fixed time slots for specific courses (MDM/Minor etc.)
 - ROOM DETAILS: room -> type (CR/LAB)

Accepts either the combined xlsx (multiple sheets) or individual CSVs.
All time strings like "9-10" are normalized into a canonical slot id.
"""
from __future__ import annotations
import io
import re
from typing import Optional
import pandas as pd

# Canonical 8 period slots for the day (08:00 - 16:00 with 13:00-14:00 lunch)
DEFAULT_SLOTS = ["8-9", "9-10", "10-11", "11-12", "12-1", "1-2", "2-3", "3-4"]
LUNCH_SLOT = "1-2"

DAY_CODES = ["M", "T", "W", "R", "F"]  # Mon Tue Wed Thu Fri


def _decode_excel_time_cell(val) -> Optional[str]:
    """
    The source workbook stores time-range strings like '9-10' but Excel
    silently re-interprets them as dates (e.g. '9-10' -> Oct 9).
    We decode a datetime back into 'start-end' form using day=start, month=end.
    Plain strings are passed through unchanged (after stripping).
    """
    if val is None:
        return None
    if isinstance(val, str):
        v = val.strip()
        return v if v else None
    # pandas/openpyxl datetime
    try:
        day = val.day
        month = val.month
        return f"{day}-{month}"
    except AttributeError:
        return str(val)


# Period start-hour to slot label, for expanding multi-hour spans like "9-11" or "2-4"
_HOUR_TO_SLOT = {
    8: "8-9", 9: "9-10", 10: "10-11", 11: "11-12",
    12: "12-1", 1: "1-2", 2: "2-3", 3: "3-4", 4: "4-5",
}


def expand_slot_to_periods(slot_str: str) -> list[str]:
    """
    Expand a (possibly multi-hour) slot like '9-11', '2-4', '11-1' into the
    list of 1-hour canonical period labels it covers, e.g.
    '9-11' -> ['9-10', '10-11'], '2-4' -> ['2-3', '3-4'], '11-1' -> ['11-12', '12-1'].
    Single-hour slots like '9-10' are returned as-is (normalized).
    """
    s = normalize_slot(slot_str)
    if not s or "-" not in s:
        return [s] if s else []
    a, b = s.split("-")
    try:
        start, end = int(a), int(b)
    except ValueError:
        return [s]

    def to_24(h):
        if h in (1, 2, 3, 4):
            return h + 12
        return h

    start24 = to_24(start)
    end24 = to_24(end)
    if end24 <= start24:
        end24 += 12  # wrap (e.g. 11-1 -> 11 to 13)

    span = end24 - start24
    if span <= 1:
        return [s]

    periods = []
    cur = start24
    for _ in range(span):
        nxt = cur + 1
        cur_label = cur if cur <= 12 else cur - 12
        nxt_label = nxt if nxt <= 12 else nxt - 12
        periods.append(f"{cur_label}-{nxt_label}")
        cur = nxt
    return periods


def normalize_slot(slot_str: str) -> str:
    """Normalize a slot string like '9-10', '09-10', ' 9 - 10 ' -> '9-10'."""
    if slot_str is None:
        return ""
    s = re.sub(r"\s+", "", str(slot_str))
    parts = re.split(r"[-–—]", s)
    parts = [p.lstrip("0") or "0" for p in parts]
    return "-".join(parts)


def load_course_master(source) -> pd.DataFrame:
    """
    Load CSV1 / BASE SHEET. `source` is a file-like object or path.
    Returns a DataFrame with normalized columns.
    """
    if hasattr(source, "name") and str(source.name).lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(source, sheet_name="BASE SHEET")
    else:
        try:
            df = pd.read_excel(source, sheet_name="BASE SHEET")
        except Exception:
            source.seek(0) if hasattr(source, "seek") else None
            df = pd.read_csv(source)

    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.dropna(how="all")

    required = ["CLASS", "SEM", "BRANCH", "DIVISION", "NO OF BATCH",
                 "TYPE OF COURSE", "COURSE_NAME", "FACULTY NAME",
                 "ROOM NO", "TYPE OF SUBJECT", "NO.OF HOURS"]
    for col in required:
        if col not in df.columns:
            df[col] = None

    df["CLASS"] = df["CLASS"].astype(str).str.strip().str.upper()
    df["BRANCH"] = df["BRANCH"].astype(str).str.strip().str.upper()
    df["DIVISION"] = df["DIVISION"].astype(str).str.strip().str.upper()
    df["TYPE OF COURSE"] = df["TYPE OF COURSE"].astype(str).str.strip().str.upper()
    df["COURSE_NAME"] = df["COURSE_NAME"].astype(str).str.strip()
    df["TYPE OF SUBJECT"] = df["TYPE OF SUBJECT"].astype(str).str.strip().str.upper()
    df["ROOM NO"] = df["ROOM NO"].astype(str).str.strip().str.upper()
    df["NO.OF HOURS"] = pd.to_numeric(df["NO.OF HOURS"], errors="coerce").fillna(0)

    # FACULTY NAME may contain comma-separated list (one per batch, for practicals)
    df["FACULTY_LIST"] = df["FACULTY NAME"].apply(
        lambda x: [f.strip().upper() for f in str(x).split(",") if f.strip()] if pd.notna(x) and str(x).strip().lower() != "nan" else []
    )

    return df.reset_index(drop=True)


def load_room_details(source) -> pd.DataFrame:
    if hasattr(source, "name") and str(source.name).lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(source, sheet_name="ROOM DETAILS")
    else:
        try:
            df = pd.read_excel(source, sheet_name="ROOM DETAILS")
        except Exception:
            source.seek(0) if hasattr(source, "seek") else None
            df = pd.read_csv(source)
    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.dropna(how="all")
    df["ROOM"] = df["ROOM"].astype(str).str.strip().str.upper()
    df["TYPE"] = df["TYPE"].astype(str).str.strip().str.upper()
    return df.reset_index(drop=True)


def load_fixed_slots(source) -> pd.DataFrame:
    """Load CSV3 / MDM COURSES — fixed time slots for specific courses."""
    if hasattr(source, "name") and str(source.name).lower().endswith((".xlsx", ".xls")):
        raw = pd.read_excel(source, sheet_name="MDM COURSES", header=None)
    else:
        try:
            raw = pd.read_excel(source, sheet_name="MDM COURSES", header=None)
        except Exception:
            source.seek(0) if hasattr(source, "seek") else None
            raw = pd.read_csv(source, header=None)

    # find header row containing 'CLASS'
    header_row_idx = None
    for i, row in raw.iterrows():
        vals = [str(v).strip().upper() for v in row.tolist()]
        if "CLASS" in vals and "DAY" in vals:
            header_row_idx = i
            break
    if header_row_idx is None:
        return pd.DataFrame(columns=["CLASS", "TYPE OF COURSE", "SEM", "DAY", "SLOT"])

    header = [str(v).strip().upper() for v in raw.iloc[header_row_idx].tolist()]
    data = raw.iloc[header_row_idx + 1:].copy()
    data.columns = header + [f"EXTRA_{i}" for i in range(len(data.columns) - len(header))] \
        if len(data.columns) > len(header) else header
    data = data.dropna(subset=["CLASS"])

    out = pd.DataFrame()
    out["CLASS"] = data["CLASS"].astype(str).str.strip().str.upper()
    out["TYPE OF COURSE"] = data["TYPE OF COURSE"].astype(str).str.strip().str.upper()
    out["SEM"] = pd.to_numeric(data["SEM"], errors="coerce")
    out["DAY"] = data["DAY"].astype(str).str.strip().str.upper()
    out["SLOT"] = data["TIME"].apply(lambda v: normalize_slot(_decode_excel_time_cell(v)))
    out = out[out["CLASS"].str.lower() != "nan"]
    return out.reset_index(drop=True)


def load_faculty_busy(source) -> pd.DataFrame:
    """Load CSV2 / FACULTY NA — faculty occupied slots (faculty NOT free)."""
    if hasattr(source, "name") and str(source.name).lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(source, sheet_name="FACULTY NA")
    else:
        try:
            df = pd.read_excel(source, sheet_name="FACULTY NA")
        except Exception:
            source.seek(0) if hasattr(source, "seek") else None
            df = pd.read_csv(source)

    df.columns = [str(c).strip().upper() for c in df.columns]
    df = df.dropna(subset=[df.columns[0]])

    out = pd.DataFrame()
    out["FACULTY NAME"] = df["FACULTY NAME"].astype(str).str.strip().str.upper()
    out["DAY"] = df["DAY"].astype(str).str.strip().str.upper()
    time_col = "TIME" if "TIME" in df.columns else "TIME "
    out["SLOT"] = df[time_col].apply(lambda v: normalize_slot(_decode_excel_time_cell(v)))
    out = out[out["FACULTY NAME"].str.lower() != "nan"]
    return out.reset_index(drop=True)


def load_all_from_xlsx(file_bytes: bytes) -> dict:
    """Convenience: load all four datasets from one combined xlsx."""
    bio = io.BytesIO(file_bytes)
    bio.name = "input.xlsx"
    course_master = load_course_master(io.BytesIO(file_bytes))
    rooms = load_room_details(io.BytesIO(file_bytes))
    fixed_slots = load_fixed_slots(io.BytesIO(file_bytes))
    try:
        faculty_busy = load_faculty_busy(io.BytesIO(file_bytes))
    except Exception:
        faculty_busy = pd.DataFrame(columns=["FACULTY NAME", "DAY", "SLOT"])
    return {
        "course_master": course_master,
        "rooms": rooms,
        "fixed_slots": fixed_slots,
        "faculty_busy": faculty_busy,
    }
