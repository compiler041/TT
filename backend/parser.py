"""
parser.py — Reads the Excel workbook and produces a SchedulerInput.

Sheets:
  BASE SHEET   — Course-Faculty-Division assignments (headers row 1, data row 2+)
  ROOM DETAILS — Room name → type mapping (headers row 1, data row 2+)
  MDM COURSES  — Fixed time slots for MDM/Minor/OET (headers row 3, data row 4+)
  FACULTY NA   — Faculty blocked times (headers row 1, data row 2+)

Time encoding: Excel dates with d-m format.
  datetime(2026, 10, 9)  → d-m → "9-10"  meaning 9:00-10:00 (period 1)
  datetime(2026, 11, 10) → d-m → "10-11" meaning 10:00-11:00 (period 2)
  The day_of_month = start hour, month = end hour.

Day codes: M=MON, T=TUE, W=WED, R=THU, F=FRI
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl

from models import (
    DAYS, LUNCH_PERIOD, LUNCH_SLOTS, PERIODS_PER_DAY,
    Batch, Course, Division, Faculty, FixedSlotBlock,
    Room, SchedulerInput, Session, SubjectType,
    slot_id, mark_busy,
)


# ── Day code mapping ───────────────────────────────────────

DAY_CODE_MAP: dict[str, str] = {
    "M": "MON", "T": "TUE", "W": "WED", "R": "THU", "F": "FRI",
    "MON": "MON", "TUE": "TUE", "WED": "WED", "THU": "THU", "FRI": "FRI",
    "MONDAY": "MON", "TUESDAY": "TUE", "WEDNESDAY": "WED",
    "THURSDAY": "THU", "FRIDAY": "FRI",
}


def _parse_day_code(code: str) -> str:
    """'M' → 'MON', 'T' → 'TUE', etc."""
    key = code.strip().upper()
    if key in DAY_CODE_MAP:
        return DAY_CODE_MAP[key]
    raise ValueError(f"Unknown day code: {code!r}")


def _parse_time_from_date(dt_val: Any) -> int:
    """
    Excel stores times as dates with d-m format.
    datetime(2026, 10, 9) → day=9, month=10 → displays "9-10" → period 1 (9:00-10:00)
    Returns 1-indexed period number.
    """
    if isinstance(dt_val, datetime):
        start_hour = dt_val.day
        return _hour_to_period(start_hour)
    if isinstance(dt_val, str):
        # Try to parse "9-10" style string
        dt_val = dt_val.strip()
        if "-" in dt_val:
            parts = dt_val.split("-")
            try:
                start_hour = int(parts[0].strip())
                return _hour_to_period(start_hour)
            except ValueError:
                pass
        # Try parsing as datetime string
        try:
            dt = datetime.fromisoformat(dt_val.replace(" ", "T"))
            return _hour_to_period(dt.day)
        except (ValueError, TypeError):
            pass
    raise ValueError(f"Cannot parse time from: {dt_val!r}")


def _hour_to_period(hour: int) -> int:
    """
    Convert clock hour to 1-indexed period.
    9→1, 10→2, 11→3, 12→4, 1→5(lunch), 2→6, 3→7, 4→8, 5→9
    """
    HOUR_MAP = {
        9: 1, 10: 2, 11: 3, 12: 4,
        1: 5, 2: 6, 3: 7, 4: 8, 5: 9,
    }
    if hour in HOUR_MAP:
        return HOUR_MAP[hour]
    raise ValueError(f"Unknown hour: {hour}")


def _safe_int(val: Any) -> int:
    if val is None:
        return 0
    if isinstance(val, str):
        val = val.strip().replace("\n", "")
        if not val:
            return 0
        return int(float(val))
    return int(val)


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    # Remove trailing newlines and whitespace
    s = s.replace("\n", "").replace("\r", "").strip()
    return s


def _parse_faculty_list(fac_str: str) -> list[str]:
    """Parse comma-separated faculty names, cleaning whitespace/newlines."""
    if not fac_str or fac_str == "-":
        return []
    parts = re.split(r"[,\s]+", fac_str.replace("\n", "").strip())
    return [p.strip() for p in parts if p.strip()]


# ════════════════════════════════════════════════════════════
#  MAIN PARSE FUNCTION
# ════════════════════════════════════════════════════════════

def parse_workbook(path: str | Path) -> SchedulerInput:
    wb = openpyxl.load_workbook(str(path), data_only=True)

    divisions: dict[str, Division] = {}
    courses: dict[str, Course] = {}
    faculty: dict[str, Faculty] = {}
    rooms: dict[str, Room] = {}
    assignments: dict[str, dict[str, list[str]]] = {}
    sessions: list[Session] = []
    session_counter = 0

    # ══════════════════════════════════════════════════════
    #  ROOM DETAILS sheet
    # ══════════════════════════════════════════════════════
    if "ROOM DETAILS" in wb.sheetnames:
        ws_rooms = wb["ROOM DETAILS"]
        for row in ws_rooms.iter_rows(min_row=2, values_only=True):
            room_no = _safe_str(row[0])
            room_type_raw = _safe_str(row[1]).upper()
            if not room_no:
                continue
            room_type = "CLASSROOM" if room_type_raw == "CR" else "LAB"
            rooms[room_no] = Room(room_no=room_no, room_type=room_type)

    # ══════════════════════════════════════════════════════
    #  BASE SHEET — assignments
    # ══════════════════════════════════════════════════════
    ws_base = wb["BASE SHEET"]
    rows = list(ws_base.iter_rows(min_row=2, values_only=True))  # row 1 = headers

    for row in rows:
        if len(row) < 11:
            continue

        year        = _safe_str(row[0]).upper()
        sem         = _safe_int(row[1])
        branch      = _safe_str(row[2]).upper()
        div_label   = _safe_str(row[3]).upper()
        batch_str   = _safe_str(row[4]).upper()
        course_type = _safe_str(row[5]).upper()    # C, E, E1, E2, O, M, MDM
        course_name = _safe_str(row[6])
        fac_str     = _safe_str(row[7])
        room_no     = _safe_str(row[8])
        subj_type_str = _safe_str(row[9]).upper()  # L, P, T
        hours       = _safe_int(row[10])

        if not year or not course_name:
            continue

        # Skip ONLINE-only courses (type O) — they'll be handled via fixed slots
        is_online = (room_no.upper() == "ONLINE" or course_type == "O")

        # Parse faculty list
        fac_list = _parse_faculty_list(fac_str)

        # Normalize subject type
        if subj_type_str == "L":
            subj_type = SubjectType.THEORY
        elif subj_type_str == "P":
            subj_type = SubjectType.PRACTICAL
        elif subj_type_str == "T":
            subj_type = SubjectType.TUTORIAL
        else:
            subj_type = SubjectType.THEORY

        # Determine if this is an elective or parallel group
        is_elective = course_type in ("E", "E1", "E2", "M", "MDM", "OEHM", "OET", "O")
        elective_group = course_type if is_elective else ""

        # ── Handle BRANCH=ALL → applies to ALL divisions of that year ──
        if branch == "ALL":
            # Collect all division keys for this year
            target_div_keys = []
            for dk, dv in divisions.items():
                if dv.year == year and dv.sem == sem:
                    target_div_keys.append(dk)

            if not target_div_keys:
                # Create a placeholder ALL division if no real divisions yet
                # This will be distributed later
                div_key = f"{year}_ALL_{div_label}"
                if div_key not in divisions:
                    divisions[div_key] = Division(
                        year=year, branch="ALL", div=div_label,
                        sem=sem, batches=[]
                    )
                target_div_keys = [div_key]
        else:
            div_key = f"{year}_{branch}_{div_label}"
            target_div_keys = [div_key]

        for div_key in target_div_keys:
            # ── Division ──
            if div_key not in divisions:
                divisions[div_key] = Division(
                    year=year, branch=branch, div=div_label,
                    sem=sem, batches=[]
                )

            division = divisions[div_key]

            # ── Theory room ──
            if subj_type == SubjectType.THEORY and batch_str == "ALL":
                if room_no and room_no != "-" and room_no != "CR" and room_no != "ONLINE":
                    division.theory_room = room_no

            # ── Determine batch count and create batches ──
            if batch_str != "ALL":
                try:
                    num_batches = int(float(batch_str))
                except (ValueError, TypeError):
                    num_batches = 1

                # Ensure we have enough batch objects
                while len(division.batches) < num_batches:
                    bid = f"B{len(division.batches) + 1}"
                    division.batches.append(Batch(
                        batch_id=bid,
                        division_key=div_key,
                    ))

            # ── Course ──
            # Use div-scoped course key to distinguish same course across divisions
            if course_name not in courses:
                courses[course_name] = Course(
                    code=course_name,
                    subject_type=subj_type,
                    L=0, T=0, P=0,
                    hours_per_week=0,
                    is_elective=is_elective,
                    course_type=course_type,
                )
            course = courses[course_name]
            if subj_type == SubjectType.THEORY and batch_str == "ALL":
                course.L = max(course.L, hours)
            elif subj_type == SubjectType.PRACTICAL:
                course.P = max(course.P, hours)
            elif subj_type == SubjectType.TUTORIAL:
                course.T = max(course.T, hours)
            course.hours_per_week = course.L + course.T + course.P
            if is_elective:
                course.is_elective = True
                course.course_type = course_type

            # ── Faculty ──
            for fn in fac_list:
                if fn and fn != "-":
                    if fn not in faculty:
                        faculty[fn] = Faculty(fac_id=fn, eligible_codes=[])
                    fac = faculty[fn]
                    if course_name not in fac.eligible_codes:
                        fac.eligible_codes.append(course_name)

            # ── Room ──
            if room_no and room_no != "-" and room_no != "CR" and room_no != "ONLINE":
                if room_no not in rooms:
                    rtype = "LAB" if subj_type == SubjectType.PRACTICAL else "CLASSROOM"
                    rooms[room_no] = Room(room_no=room_no, room_type=rtype)
                if subj_type == SubjectType.PRACTICAL:
                    rooms[room_no].room_type = "LAB"

            # ── Assignments map ──
            assignments.setdefault(div_key, {}).setdefault(course_name, [])
            for fn in fac_list:
                if fn and fn not in assignments[div_key][course_name]:
                    assignments[div_key][course_name].append(fn)

            # ── Skip session generation for ONLINE courses ──
            if is_online:
                continue

            # ── Sessions ──
            if subj_type == SubjectType.THEORY and batch_str == "ALL":
                # Create `hours` theory sessions (each 1 period)
                for i in range(hours):
                    primary_fac = fac_list[0] if fac_list else ""
                    sessions.append(Session(
                        session_id=session_counter,
                        division_key=div_key,
                        course_code=course_name,
                        subject_type=SubjectType.THEORY,
                        batch_id="ALL",
                        fac_id=primary_fac,
                        room_no=division.theory_room or room_no if room_no != "CR" else "",
                        duration=1,
                        session_index=i,
                        elective_group=elective_group,
                        course_type=course_type,
                    ))
                    session_counter += 1

            elif subj_type == SubjectType.PRACTICAL:
                # Create per-batch sessions
                num_batches = len(division.batches) if division.batches else 1
                try:
                    declared_batches = int(float(batch_str))
                except (ValueError, TypeError):
                    declared_batches = num_batches

                actual_batches = min(declared_batches, len(division.batches)) if division.batches else 1

                # Practical: hours=2 → 1 session of duration 2
                #            hours=4 → 2 sessions of duration 2
                num_sessions_per_batch = max(1, hours // 2)
                session_duration = 2

                for sess_idx in range(num_sessions_per_batch):
                    for batch_idx in range(actual_batches):
                        batch_id = division.batches[batch_idx].batch_id if division.batches else "ALL"
                        # Assign faculty: distribute among batches
                        fac_for_batch = ""
                        if fac_list:
                            fac_for_batch = fac_list[batch_idx % len(fac_list)]

                        sessions.append(Session(
                            session_id=session_counter,
                            division_key=div_key,
                            course_code=course_name,
                            subject_type=SubjectType.PRACTICAL,
                            batch_id=batch_id,
                            fac_id=fac_for_batch,
                            room_no=room_no if room_no != "CR" else "",
                            duration=session_duration,
                            session_index=sess_idx,
                            elective_group=elective_group,
                            course_type=course_type,
                        ))
                        session_counter += 1

            elif subj_type == SubjectType.TUTORIAL:
                # Tutorial: create per-batch sessions
                num_batches_for_tut = len(division.batches) if division.batches else 1
                try:
                    declared_batches = int(float(batch_str))
                except (ValueError, TypeError):
                    declared_batches = num_batches_for_tut

                actual_batches = min(declared_batches, len(division.batches)) if division.batches else 1

                for batch_idx in range(actual_batches):
                    batch_id = division.batches[batch_idx].batch_id if division.batches else "ALL"
                    fac_for_batch = ""
                    if fac_list:
                        fac_for_batch = fac_list[batch_idx % len(fac_list)]

                    sessions.append(Session(
                        session_id=session_counter,
                        division_key=div_key,
                        course_code=course_name,
                        subject_type=SubjectType.TUTORIAL,
                        batch_id=batch_id,
                        fac_id=fac_for_batch,
                        room_no=room_no if room_no not in ("CR", "-") else "",
                        duration=1,
                        session_index=0,
                        elective_group=elective_group,
                        course_type=course_type,
                    ))
                    session_counter += 1

    # ══════════════════════════════════════════════════════
    #  FACULTY NA — Faculty blocked times
    # ══════════════════════════════════════════════════════
    if "FACULTY NA" in wb.sheetnames:
        ws_fac = wb["FACULTY NA"]
        for row in ws_fac.iter_rows(min_row=2, values_only=True):
            fac_name = _safe_str(row[0])
            day_code = _safe_str(row[1])
            time_val = row[2]  # Keep raw for datetime parsing

            if not fac_name or not day_code or time_val is None:
                continue
            try:
                day = _parse_day_code(day_code)
                period = _parse_time_from_date(time_val)
                sid = slot_id(day, period)

                if fac_name not in faculty:
                    faculty[fac_name] = Faculty(fac_id=fac_name, eligible_codes=[])
                faculty[fac_name].blocked_mask = mark_busy(
                    faculty[fac_name].blocked_mask, sid
                )
            except (ValueError, IndexError) as e:
                print(f"[WARN] Could not parse faculty blocked: {row} — {e}")

    # ══════════════════════════════════════════════════════
    #  MDM COURSES — Fixed time blocks
    # ══════════════════════════════════════════════════════
    fixed_blocks: list[FixedSlotBlock] = []
    if "MDM COURSES" in wb.sheetnames:
        ws_mdm = wb["MDM COURSES"]
        for row in ws_mdm.iter_rows(min_row=4, values_only=True):
            year_raw    = _safe_str(row[0]).upper()
            course_type = _safe_str(row[1]).upper()
            sem_raw     = row[2]
            day_code    = _safe_str(row[3])
            time_val    = row[4]  # Keep raw for datetime parsing

            if not year_raw or not day_code or time_val is None:
                continue
            if day_code == ".":
                continue

            # Parse semester
            sem = _safe_int(sem_raw)

            try:
                day = _parse_day_code(day_code)
                period = _parse_time_from_date(time_val)
                sid = slot_id(day, period)

                key = (year_raw, sem, course_type)
                found = False
                for fb in fixed_blocks:
                    if (fb.year, fb.sem, fb.course_type) == key:
                        if sid not in fb.slot_ids:
                            fb.slot_ids.append(sid)
                        found = True
                        break
                if not found:
                    block = FixedSlotBlock(
                        year=year_raw,
                        sem=sem,
                        course_type=course_type,
                        slot_ids=[sid],
                    )
                    fixed_blocks.append(block)

            except (ValueError, IndexError) as e:
                print(f"[WARN] Could not parse fixed slot: {row} — {e}")

    # ── Burn lunch into all division blocked masks ─────────
    for div in divisions.values():
        for lunch_sid in LUNCH_SLOTS:
            div.blocked_mask = mark_busy(div.blocked_mask, lunch_sid)

    # ── Distribute ALL-branch sessions to real divisions ──
    # Find sessions targeting ALL divisions and clone them
    all_div_keys = [dk for dk in divisions if "_ALL_" in dk]
    real_div_map: dict[str, list[str]] = {}  # year → list of real div keys

    for dk, dv in divisions.items():
        if dv.branch != "ALL":
            real_div_map.setdefault(dv.year, []).append(dk)

    # Reassign sessions from ALL divisions to real divisions
    sessions_to_add: list[Session] = []
    sessions_to_remove: set[int] = set()

    for s in sessions:
        if "_ALL_" in s.division_key:
            year = s.division_key.split("_")[0]
            real_divs = real_div_map.get(year, [])
            if real_divs:
                sessions_to_remove.add(s.session_id)
                for rd in real_divs:
                    sessions_to_add.append(Session(
                        session_id=session_counter,
                        division_key=rd,
                        course_code=s.course_code,
                        subject_type=s.subject_type,
                        batch_id=s.batch_id,
                        fac_id=s.fac_id,
                        room_no=s.room_no,
                        duration=s.duration,
                        session_index=s.session_index,
                        elective_group=s.elective_group,
                        course_type=s.course_type,
                    ))
                    session_counter += 1

                    # Also copy assignments
                    if s.division_key in assignments and s.course_code in assignments[s.division_key]:
                        fac_list = assignments[s.division_key][s.course_code]
                        assignments.setdefault(rd, {}).setdefault(s.course_code, [])
                        for fn in fac_list:
                            if fn not in assignments[rd][s.course_code]:
                                assignments[rd][s.course_code].append(fn)

    sessions = [s for s in sessions if s.session_id not in sessions_to_remove]
    sessions.extend(sessions_to_add)

    # Re-number session IDs
    for i, s in enumerate(sessions):
        s.session_id = i

    # Remove ALL-branch divisions (they've been distributed)
    for dk in all_div_keys:
        if dk in divisions:
            del divisions[dk]

    wb.close()

    return SchedulerInput(
        divisions=divisions,
        courses=courses,
        faculty=faculty,
        rooms=rooms,
        assignments=assignments,
        fixed_blocks=fixed_blocks,
        sessions=sessions,
    )


# ════════════════════════════════════════════════════════════
#  CLI test
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    path = sys.argv[1] if len(sys.argv) > 1 else "../TT_fomat (4).xlsx"
    data = parse_workbook(path)
    print(f"\n{'='*60}")
    print(f"Divisions:  {len(data.divisions)}")
    for k, d in data.divisions.items():
        batches = [b.batch_id for b in d.batches]
        blocked = bin(d.blocked_mask).count("1")
        print(f"  {k:25s}  sem={d.sem}  batches={batches}  theory_room={d.theory_room}  blocked={blocked}")
    print(f"\nCourses:    {len(data.courses)}")
    for c in data.courses.values():
        print(f"  {c.code:15s}  L={c.L} T={c.T} P={c.P}  elective={c.is_elective}  type={c.course_type}")
    print(f"\nFaculty:    {len(data.faculty)}")
    for f in data.faculty.values():
        blocked = bin(f.blocked_mask).count("1")
        print(f"  {f.fac_id:6s}  eligible={f.eligible_codes}  blocked_slots={blocked}")
    print(f"\nRooms:      {len(data.rooms)}")
    for r in data.rooms.values():
        print(f"  {r.room_no:8s}  type={r.room_type}")
    print(f"\nSessions:   {len(data.sessions)}")
    for s in data.sessions:
        print(f"  S{s.session_id:3d}  {s.division_key:25s}  {s.course_code:12s}  {s.subject_type.value:10s}  batch={s.batch_id:4s}  fac={s.fac_id:5s}  room={s.room_no:8s}  dur={s.duration}  egrp={s.elective_group}")
    print(f"Fixed blocks: {len(data.fixed_blocks)}")
    for fb in data.fixed_blocks:
        print(f"  {fb.year} sem={fb.sem} {fb.course_type} slots={fb.slot_ids}")
    print(f"{'='*60}")
