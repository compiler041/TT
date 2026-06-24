"""
Loader: reads the uploaded .xlsx and returns clean lists of domain objects.

This file is intentionally "dumb" - it does no scheduling, no constraint
checking. Its only job is: messy spreadsheet rows in, clean Python objects out.
"""

from openpyxl import load_workbook
from datetime import datetime

from app.models import Subject, Classroom, FixedSlot, FacultyUnavailability, DivisionMeta, PracticalSession
from collections import defaultdict
import math


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------

def _clean(value) -> str:
    """Strip whitespace, handle None -> ''."""
    if value is None:
        return ""
    return str(value).strip()


def _split_faculty(raw: str) -> list[str]:
    """'RTD,AYH,AYH' -> ['RTD', 'AYH', 'AYH']. Strips whitespace per item."""
    if not raw:
        return []
    return [f.strip() for f in raw.split(",") if f.strip()]


def _period_from_cell(value) -> int:
    """
    Converts a TIME-column cell into a period int (1-9).

    Once you fix the sheet to store plain integers 1-8, this is a no-op cast.
    Kept as its own function so the conversion lives in exactly one place -
    if the sheet format changes again, you only edit this.
    """
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, datetime):
        # Excel encodes time as datetime(year, month, day)
        # where day = start hour. Map: 9->1, 10->2, 11->3, 12->4, 1->5, 2->6, 3->7, 4->8, 5->9
        hour = value.day
        HOUR_TO_PERIOD = {9:1, 10:2, 11:3, 12:4, 1:5, 2:6, 3:7, 4:8, 5:9}
        if hour in HOUR_TO_PERIOD:
            return HOUR_TO_PERIOD[hour]
        raise ValueError(f"Unmapped hour {hour} from datetime {value}")
    raise ValueError(f"Unrecognized period value: {value!r}")


# ---------------------------------------------------------------------
# Sheet 1: BASE SHEET -> list[Subject]
# ---------------------------------------------------------------------

def load_subjects(path: str, sheet_name: str = "BASE SHEET") -> list[Subject]:
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]

    rows = ws.iter_rows(min_row=2, values_only=True)  # skip header
    subjects: list[Subject] = []

    for row in rows:
        if row is None or row[0] is None:
            continue  # skip blank rows

        (year, sem, branch, division, no_of_batch,
         course_type, name, faculty_raw, room,
         subject_type, hours) = row[:11]

        year = _clean(year)
        if not year:
            continue

        faculty_codes = _split_faculty(_clean(faculty_raw))
        batch_label = _clean(no_of_batch)

        if batch_label.upper() == "ALL" or not faculty_codes:
            # Single entry, whole class together (or no faculty assigned yet, e.g. OEHM)
            subjects.append(Subject(
                year=year,
                semester=float(sem) if sem else 0.0,
                branch=_clean(branch),
                division=_clean(division),
                batch="ALL",
                course_type=_clean(course_type),
                name=_clean(name),
                faculty_codes=faculty_codes,
                room=_clean(room),
                subject_type=_clean(subject_type),
                hours_per_week=float(hours) if hours else 0.0,
            ))
        else:
            # Explode into one Subject per faculty code (= one per batch)
            for i, fac in enumerate(faculty_codes, start=1):
                subjects.append(Subject(
                    year=year,
                    semester=float(sem) if sem else 0.0,
                    branch=_clean(branch),
                    division=_clean(division),
                    batch=str(i),
                    course_type=_clean(course_type),
                    name=_clean(name),
                    faculty_codes=[fac],
                    room=_clean(room),
                    subject_type=_clean(subject_type),
                    hours_per_week=float(hours) if hours else 0.0,
                ))

    return subjects


# ---------------------------------------------------------------------
# Sheet 2: ROOM DETAILS -> list[Classroom]
# ---------------------------------------------------------------------

def load_classrooms(path: str, sheet_name: str = "ROOM DETAILS") -> list[Classroom]:
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]

    classrooms: list[Classroom] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row is None or row[0] is None:
            continue
        room_id, room_type = row[0], row[1]
        classrooms.append(Classroom(
            room_id=_clean(room_id),
            room_type=_clean(room_type),
        ))
    return classrooms


# ---------------------------------------------------------------------
# Sheet 3: MDM COURSES -> list[FixedSlot]
# ---------------------------------------------------------------------

def load_fixed_slots(path: str, sheet_name: str = "MDM COURSES") -> list[FixedSlot]:
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]

    fixed_slots: list[FixedSlot] = []
    # Row 1 = title, Row 2 = blank/title continuation, Row 3 = header, data starts row 4
    for row in ws.iter_rows(min_row=4, values_only=True):
        if row is None or row[0] is None:
            continue
        year, course_type, sem, day, period_val = row[:5]
        year = _clean(year)
        day = _clean(day)
        if not year or not day:
            continue
        fixed_slots.append(FixedSlot(
            year=year,
            course_type=_clean(course_type),
            semester=float(sem) if sem else 0.0,
            day=day,
            period=_period_from_cell(period_val),
        ))
    return fixed_slots


# ---------------------------------------------------------------------
# Sheet 4: FACULTY NA -> list[FacultyUnavailability]
# ---------------------------------------------------------------------

def load_faculty_unavailability(
    path: str, sheet_name: str = "FACULTY NA"
) -> list[FacultyUnavailability]:
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet_name]

    unavailable: list[FacultyUnavailability] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row is None or row[0] is None:
            continue
        faculty_code, day, period_val = row[:3]
        faculty_code = _clean(faculty_code)
        day = _clean(day)
        if not faculty_code or not day:
            continue
        unavailable.append(FacultyUnavailability(
            faculty_code=faculty_code,
            day=day,
            period=_period_from_cell(period_val),
        ))
    return unavailable


def extract_division_meta(subjects: list[Subject]) -> dict[tuple[str, str, str], DivisionMeta]:
    meta_dict = {}
    by_class = defaultdict(list)
    for s in subjects:
        if s.branch != "ALL":
            by_class[s.class_key].append(s)
            
    for class_key, class_subjects in by_class.items():
        batch_count = 1
        theory_room = "CR"
        
        for s in class_subjects:
            if s.batch != "ALL" and s.batch.isdigit():
                batch_count = max(batch_count, int(s.batch))
            if s.subject_type == "L" and s.room != "CR" and s.room != "ONLINE":
                theory_room = s.room
                
        meta_dict[class_key] = DivisionMeta(
            class_key=class_key,
            batch_count=batch_count,
            theory_room=theory_room
        )
        
    return meta_dict


def _build_sessions(class_subjects: list[Subject], subject_type: str, batch_count: int, class_key: tuple[str, str, str]) -> list[PracticalSession]:
    sessions = []
    valid_subjects = [s for s in class_subjects if s.subject_type == subject_type and s.batch.isdigit()]
    if not valid_subjects: return []
    
    # Expand subjects by hours
    blocks_by_name = []
    by_name = defaultdict(list)
    for s in valid_subjects:
        by_name[s.name].append(s)
        
    for name, subs in by_name.items():
        # assume all batches have same hours for the same subject
        hours = subs[0].hours_per_week
        # practicals are 2 hrs per block, tutorials 1 hr
        hrs_per_block = 2 if subject_type == "P" else 1
        num_blocks = int(hours // hrs_per_block)
        blocks_by_name.append([f"{name}__block{i}" for i in range(num_blocks)])
            
    # Round-robin to spread identical subjects
    expanded_names = []
    while any(blocks_by_name):
        for blocks in blocks_by_name:
            if blocks:
                expanded_names.append(blocks.pop(0))
                
    num_subjects = len(expanded_names)
    if num_subjects == 0:
        return []

    num_sessions = num_subjects

    for session_idx in range(num_sessions):
        assignments = []
        for batch_idx in range(1, batch_count + 1):
            sub_idx = (session_idx + batch_idx - 1) % num_subjects
            exp_name = expanded_names[sub_idx]
            real_name = exp_name.split("__block")[0]
            
            subject_entries = [s for s in by_name[real_name] if s.batch == str(batch_idx)]
            if subject_entries:
                subj = subject_entries[0]
                fac = subj.faculty_codes[0] if subj.faculty_codes else ""
                assignments.append((subj, fac))
                
        if assignments:
            sessions.append(PracticalSession(division_key=class_key, assignments=assignments))
            
    return sessions


def build_practical_sessions(subjects: list[Subject], meta_dict: dict[tuple[str, str, str], DivisionMeta]) -> list[PracticalSession]:
    sessions = []
    by_class = defaultdict(list)
    for s in subjects:
        if s.branch != "ALL":
            by_class[s.class_key].append(s)
            
    for class_key, class_subjects in by_class.items():
        if class_key in meta_dict:
            batch_count = meta_dict[class_key].batch_count
            div_sessions = _build_sessions(class_subjects, "P", batch_count, class_key)
            sessions.extend(div_sessions)
            
    return sessions


def build_tutorial_sessions(subjects: list[Subject], meta_dict: dict[tuple[str, str, str], DivisionMeta]) -> list[PracticalSession]:
    sessions = []
    by_class = defaultdict(list)
    for s in subjects:
        if s.branch != "ALL":
            by_class[s.class_key].append(s)
            
    for class_key, class_subjects in by_class.items():
        if class_key in meta_dict:
            batch_count = meta_dict[class_key].batch_count
            div_sessions = _build_sessions(class_subjects, "T", batch_count, class_key)
            sessions.extend(div_sessions)
            
    return sessions


# ---------------------------------------------------------------------
# Convenience: load everything at once
# ---------------------------------------------------------------------

def load_all(path: str):
    return {
        "subjects": load_subjects(path),
        "classrooms": load_classrooms(path),
        "fixed_slots": load_fixed_slots(path),
        "faculty_unavailability": load_faculty_unavailability(path),
    }
