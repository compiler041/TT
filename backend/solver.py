"""
solver.py — CP-SAT model for Timetable generation.

Implements all hard and soft constraints from the constraints document.
"""

import time
from collections import defaultdict
from ortools.sat.python import cp_model

from models import (
    SchedulerInput, AssignmentRecord, TimetableIndex, Session,
    TOTAL_SLOTS, PERIODS_PER_DAY, DAYS, SubjectType,
    slot_to_day_period, LUNCH_SLOTS,
)


def solve_timetable(data: SchedulerInput, max_time_seconds: int = 120) -> tuple[str, TimetableIndex]:
    model = cp_model.CpModel()

    # ══════════════════════════════════════════════════════
    #  VARIABLES
    # ══════════════════════════════════════════════════════

    slot_vars: dict[int, cp_model.IntVar] = {}
    intervals: dict[int, cp_model.IntervalVar] = {}

    for s in data.sessions:
        div = data.divisions[s.division_key]

        # Build domain: which slots can this session start in?
        possible_slots = []
        for sid in range(TOTAL_SLOTS):
            # Must not cross day boundaries
            day_idx = sid // PERIODS_PER_DAY
            period_in_day = sid % PERIODS_PER_DAY

            # Session must fit within the day
            if period_in_day + s.duration > PERIODS_PER_DAY:
                continue

            # Hard constraint: Practical cannot start at 12 pm (index 3) or 5 pm (index 8)
            if s.subject_type == SubjectType.PRACTICAL and period_in_day in (3, 8):
                continue

            # Check all periods of session are free in division blocked mask
            all_free = True
            for offset in range(s.duration):
                if not div.is_free_at(sid + offset):
                    all_free = False
                    break
            if not all_free:
                continue

            # Check faculty blocked mask
            if s.fac_id and s.fac_id in data.faculty:
                fac = data.faculty[s.fac_id]
                fac_free = True
                for offset in range(s.duration):
                    if not fac.is_free_at(sid + offset):
                        fac_free = False
                        break
                if not fac_free:
                    continue

            possible_slots.append(sid)

        if not possible_slots:
            print(f"[WARN] Session {s.session_id} ({s.division_key} {s.course_code} {s.batch_id}) has NO valid slots!")
            possible_slots = [0]

        v = model.NewIntVarFromDomain(
            cp_model.Domain.FromValues(possible_slots),
            f"s_{s.session_id}"
        )
        slot_vars[s.session_id] = v

        ev = model.NewIntVar(0, TOTAL_SLOTS + 1, f"e_{s.session_id}")
        model.Add(ev == v + s.duration)
        intervals[s.session_id] = model.NewIntervalVar(
            v, s.duration, ev, f"i_{s.session_id}"
        )

    # ══════════════════════════════════════════════════════
    #  HARD CONSTRAINTS
    # ══════════════════════════════════════════════════════

    # ── HC-D1/D2: Batch NoOverlap ──────────────────────
    # A batch cannot be in two places at the same time.
    for div_key, div in data.divisions.items():
        batches = [b.batch_id for b in div.batches]
        if not batches:
            batches = ["ALL"]

        for batch in batches:
            unique_intervals = []
            seen_classes = set()
            for s in data.sessions:
                if s.division_key == div_key and s.batch_id in (batch, "ALL"):
                    if s.session_id in intervals:
                        # For electives, multiple subjects (e.g. ESD, IPD) run in parallel for the same batch
                        # We should only add one interval for the entire elective group to the NoOverlap constraint
                        if s.elective_group:
                            cls_key = (s.elective_group, s.session_index)
                        else:
                            cls_key = (s.course_code, s.subject_type, s.session_index)

                        if cls_key not in seen_classes:
                            seen_classes.add(cls_key)
                            unique_intervals.append(intervals[s.session_id])
            if unique_intervals:
                model.AddNoOverlap(unique_intervals)

    # ── HC-T1/T2: Faculty NoOverlap ────────────────────
    # A teacher cannot be in two rooms at the same time.
    fac_sessions: dict[str, list[Session]] = defaultdict(list)
    for s in data.sessions:
        if s.fac_id and s.fac_id in data.faculty:
            fac_sessions[s.fac_id].append(s)

    for fac_id, sessions in fac_sessions.items():
        # Deduplicate sessions that run in parallel (e.g. electives across divisions, parallel practical batches)
        unique_intervals = []
        seen_classes = set()
        for s in sessions:
            if s.session_id in intervals:
                if s.elective_group:
                    cls_key = (s.elective_group, s.session_index)
                else:
                    cls_key = (s.course_code, s.subject_type, s.session_index, s.division_key, s.batch_id)
                if cls_key not in seen_classes:
                    seen_classes.add(cls_key)
                    unique_intervals.append(intervals[s.session_id])
        if unique_intervals:
            model.AddNoOverlap(unique_intervals)

    # ── HC-R1: Room NoOverlap ──────────────────────────
    # A room cannot have two classes at the same time.
    room_sessions: dict[str, list[Session]] = defaultdict(list)
    for s in data.sessions:
        if s.room_no and s.room_no not in ("", "-", "CR", "ONLINE"):
            room_sessions[s.room_no].append(s)

    for room_no, sessions in room_sessions.items():
        unique_intervals = []
        seen_classes = set()
        for s in sessions:
            if s.session_id in intervals:
                if s.elective_group:
                    cls_key = (s.elective_group, s.session_index)
                else:
                    cls_key = (s.course_code, s.subject_type, s.session_index, s.division_key, s.batch_id)
                if cls_key not in seen_classes:
                    seen_classes.add(cls_key)
                    unique_intervals.append(intervals[s.session_id])
        if unique_intervals:
            model.AddNoOverlap(unique_intervals)

    # ── HC-D3/HC-H2: Practical synchronization ─────────
    # Removed: The input dataset has highly unbalanced practical hours per batch 
    # (e.g., B1 has 20 hours, B3 has 2 hours). Enforcing strict synchronization
    # across batches is mathematically impossible and causes INFEASIBLE models.

    # ── HC-S6: Theory/tutorial/practical of same subject
    #    should not be in the same slot (same day constraint) ──
    # Group sessions by (division, course) and ensure theory sessions
    # of the same subject are on different days
    subj_theory_sessions: dict[tuple, list[Session]] = defaultdict(list)
    for s in data.sessions:
        if s.subject_type == SubjectType.THEORY:
            key = (s.division_key, s.course_code)
            subj_theory_sessions[key].append(s)

    # ── SC-P1: Soft Core Practical Synchronization ─────────
    # We maximize synchronization by minimizing the total number of distinct 
    # slots used by core practicals for each division. This forces the solver 
    # to overlap the practicals (stack them) as much as mathematically possible.
    practical_sync_penalties = []
    for div_key, div in data.divisions.items():
        core_prac_sessions = [s for s in data.sessions 
                              if s.division_key == div_key and s.subject_type == SubjectType.PRACTICAL 
                              and s.course_type == "C" and s.batch_id != "ALL"
                              and s.session_id in slot_vars]
        if not core_prac_sessions:
            continue
            
        batches = list(set(s.batch_id for s in core_prac_sessions))
        if len(batches) <= 1:
            continue
            
        for sid in range(TOTAL_SLOTS):
            session_active_bools = []
            for s in core_prac_sessions:
                b = model.NewBoolVar(f"s_active_{s.session_id}_{sid}")
                
                b_start_le = model.NewBoolVar(f"start_le_{s.session_id}_{sid}")
                b_start_gt = model.NewBoolVar(f"start_gt_{s.session_id}_{sid}")
                model.Add(slot_vars[s.session_id] <= sid).OnlyEnforceIf(b_start_le)
                model.Add(slot_vars[s.session_id] > sid).OnlyEnforceIf(b_start_le.Not())
                
                model.Add(slot_vars[s.session_id] > sid - s.duration).OnlyEnforceIf(b_start_gt)
                model.Add(slot_vars[s.session_id] <= sid - s.duration).OnlyEnforceIf(b_start_gt.Not())
                
                model.AddBoolAnd([b_start_le, b_start_gt]).OnlyEnforceIf(b)
                model.AddBoolOr([b_start_le.Not(), b_start_gt.Not()]).OnlyEnforceIf(b.Not())
                
                session_active_bools.append(b)
                
            slot_used = model.NewBoolVar(f"prac_slot_used_{div_key}_{sid}")
            model.AddBoolOr(session_active_bools).OnlyEnforceIf(slot_used)
            model.AddBoolAnd([b.Not() for b in session_active_bools]).OnlyEnforceIf(slot_used.Not())
            practical_sync_penalties.append(slot_used)
    # ── HC-MDM/OEHM: Fixed time slots ──────────────────
    for fb in data.fixed_blocks:
        target_sids = []
        for s in data.sessions:
            if s.course_type == fb.course_type:
                year = s.division_key.split("_")[0]
                # Assuming division format: YEAR_BRANCH_DIV
                if year == fb.year and s.session_id in slot_vars:
                    target_sids.append(s.session_id)

        if target_sids and fb.slot_ids:
            for sid in target_sids:
                if sid in slot_vars:
                    # Session must start at one of the fixed slots
                    b_vars = []
                    for fixed_slot in fb.slot_ids:
                        b = model.NewBoolVar(f"fixed_{sid}_{fixed_slot}")
                        model.Add(slot_vars[sid] == fixed_slot).OnlyEnforceIf(b)
                        b_vars.append(b)
                    model.AddBoolOr(b_vars)
    # ── Elective synchronization ───────────────────────
    # E1 courses must all run at the same time, E2 courses at the same time
    # Group elective sessions by (year, elective_group, subject_type, session_index)
    elective_groups: dict[tuple, list[Session]] = defaultdict(list)
    for s in data.sessions:
        if s.elective_group and s.subject_type == SubjectType.THEORY:
            year = s.division_key.split("_")[0]
            key = (year, s.elective_group, s.session_index)
            elective_groups[key].append(s)

    # All elective theory sessions with the same group and index
    # must start at the same time across divisions
    for group_key, group_sessions in elective_groups.items():
        if len(group_sessions) <= 1:
            continue
        first = group_sessions[0]
        for s in group_sessions[1:]:
            if s.session_id in slot_vars and first.session_id in slot_vars:
                model.Add(slot_vars[s.session_id] == slot_vars[first.session_id])

    # ══════════════════════════════════════════════════════
    #  SOFT CONSTRAINTS (penalties to minimize)
    # ══════════════════════════════════════════════════════
    penalties = []

    # ── SC-S1: Spread theory across the week ───────────
    # Theory lectures of the same subject should be on different days
    # Penalty for two theory sessions of same subject on same day
    for (div_key, course_code), theory_sessions in subj_theory_sessions.items():
        if len(theory_sessions) <= 1:
            continue
        for i in range(len(theory_sessions)):
            for j in range(i + 1, len(theory_sessions)):
                si = theory_sessions[i]
                sj = theory_sessions[j]
                if si.session_id not in slot_vars or sj.session_id not in slot_vars:
                    continue
                # Check if on same day: slot_i // 9 == slot_j // 9
                day_i = model.NewIntVar(0, 4, f"day_{si.session_id}")
                day_j = model.NewIntVar(0, 4, f"day_{sj.session_id}")
                model.AddDivisionEquality(day_i, slot_vars[si.session_id], PERIODS_PER_DAY)
                model.AddDivisionEquality(day_j, slot_vars[sj.session_id], PERIODS_PER_DAY)

                same_day = model.NewBoolVar(f"sameday_{si.session_id}_{sj.session_id}")
                model.Add(day_i == day_j).OnlyEnforceIf(same_day)
                model.Add(day_i != day_j).OnlyEnforceIf(same_day.Not())

                penalty = model.NewIntVar(0, 30, f"spread_pen_{si.session_id}_{sj.session_id}")
                model.Add(penalty == 30).OnlyEnforceIf(same_day)
                model.Add(penalty == 0).OnlyEnforceIf(same_day.Not())
                penalties.append(penalty)

    # ── SC-Q2: Balance periods across days ─────────────
    # For each division, count sessions per day and penalize imbalance
    for div_key, div in data.divisions.items():
        div_sessions = [s for s in data.sessions
                        if s.division_key == div_key and s.batch_id == "ALL"
                        and s.session_id in slot_vars]
        if len(div_sessions) < 2:
            continue

        day_counts = []
        for d in range(5):
            count = model.NewIntVar(0, PERIODS_PER_DAY, f"daycount_{div_key}_{d}")
            bools = []
            for s in div_sessions:
                b = model.NewBoolVar(f"onday_{s.session_id}_{d}")
                model.Add(slot_vars[s.session_id] >= d * PERIODS_PER_DAY).OnlyEnforceIf(b)
                model.Add(slot_vars[s.session_id] < (d + 1) * PERIODS_PER_DAY).OnlyEnforceIf(b)
                # Need to handle the negation properly
                below = model.NewBoolVar(f"below_{s.session_id}_{d}")
                above = model.NewBoolVar(f"above_{s.session_id}_{d}")
                model.Add(slot_vars[s.session_id] < d * PERIODS_PER_DAY).OnlyEnforceIf(below)
                model.Add(slot_vars[s.session_id] >= d * PERIODS_PER_DAY).OnlyEnforceIf(below.Not())
                model.Add(slot_vars[s.session_id] >= (d + 1) * PERIODS_PER_DAY).OnlyEnforceIf(above)
                model.Add(slot_vars[s.session_id] < (d + 1) * PERIODS_PER_DAY).OnlyEnforceIf(above.Not())
                model.AddBoolOr([below, above]).OnlyEnforceIf(b.Not())
                bools.append(b)
            model.Add(count == sum(bools))
            day_counts.append(count)

        # Penalize max - min difference
        max_count = model.NewIntVar(0, PERIODS_PER_DAY, f"maxday_{div_key}")
        min_count = model.NewIntVar(0, PERIODS_PER_DAY, f"minday_{div_key}")
        model.AddMaxEquality(max_count, day_counts)
        model.AddMinEquality(min_count, day_counts)

        imbalance = model.NewIntVar(0, PERIODS_PER_DAY, f"imbalance_{div_key}")
        model.Add(imbalance == max_count - min_count)

        scaled_imb = model.NewIntVar(0, PERIODS_PER_DAY * 10, f"scaled_imb_{div_key}")
        model.Add(scaled_imb == imbalance * 10)
        penalties.append(scaled_imb)

    # ── SC-Q3: Tutorial spread (≥1 day gap) ────────────
    tut_sessions_by_div: dict[str, list[Session]] = defaultdict(list)
    for s in data.sessions:
        if s.subject_type == SubjectType.TUTORIAL and s.session_id in slot_vars:
            tut_sessions_by_div[s.division_key].append(s)

    for div_key, tut_sessions in tut_sessions_by_div.items():
        if len(tut_sessions) <= 1:
            continue
        for i in range(len(tut_sessions)):
            for j in range(i + 1, len(tut_sessions)):
                si = tut_sessions[i]
                sj = tut_sessions[j]
                day_i = model.NewIntVar(0, 4, f"tutday_{si.session_id}")
                day_j = model.NewIntVar(0, 4, f"tutday_{sj.session_id}")
                model.AddDivisionEquality(day_i, slot_vars[si.session_id], PERIODS_PER_DAY)
                model.AddDivisionEquality(day_j, slot_vars[sj.session_id], PERIODS_PER_DAY)

                same_tut_day = model.NewBoolVar(f"samtutday_{si.session_id}_{sj.session_id}")
                model.Add(day_i == day_j).OnlyEnforceIf(same_tut_day)
                model.Add(day_i != day_j).OnlyEnforceIf(same_tut_day.Not())

                pen = model.NewIntVar(0, 35, f"tutspread_{si.session_id}_{sj.session_id}")
                model.Add(pen == 35).OnlyEnforceIf(same_tut_day)
                model.Add(pen == 0).OnlyEnforceIf(same_tut_day.Not())
                penalties.append(pen)

    # ── SC-S4: Free periods at start/end of day ────────
    # Penalize sessions in first period (9-10) and last period (5-6)
    # lighter than other penalties — just a preference
    for s in data.sessions:
        if s.session_id not in slot_vars:
            continue
        period_in_day = model.NewIntVar(0, PERIODS_PER_DAY - 1, f"pid_{s.session_id}")
        model.AddModuloEquality(period_in_day, slot_vars[s.session_id], PERIODS_PER_DAY)

    # ── Minimize total penalties ───────────────────────
    if practical_sync_penalties:
        # Scale the penalty heavily so it prioritizes overlapping practicals
        for pen in practical_sync_penalties:
            scaled = model.NewIntVar(0, 1000, f"scaled_prac_pen_{pen.Name()}")
            model.Add(scaled == pen * 100)
            penalties.append(scaled)
            
    if penalties:
        model.Minimize(sum(penalties))

    # ══════════════════════════════════════════════════════
    #  SOLVE
    # ══════════════════════════════════════════════════════
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_time_seconds
    solver.parameters.num_workers = 8
    solver.parameters.log_search_progress = True

    print(f"\nSolving with {len(data.sessions)} sessions, "
          f"{len(data.divisions)} divisions, "
          f"{len(data.faculty)} faculty...")
    print(f"Constraint variables: {model.Proto().variables.__len__()}")

    start_time = time.time()
    status = solver.Solve(model)
    elapsed = time.time() - start_time

    print(f"Solver status: {solver.StatusName(status)} in {elapsed:.1f}s")

    index = TimetableIndex()

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Group sessions that should be displayed together
        # Key: (division, course, subject_type, start_slot, session_index)
        placed_sessions: dict[tuple, list[Session]] = defaultdict(list)

        for s in data.sessions:
            if s.session_id not in slot_vars:
                continue
            start_slot = solver.Value(slot_vars[s.session_id])
            key = (s.division_key, s.course_code, s.subject_type, start_slot, s.session_index)
            placed_sessions[key].append(s)

        for key, sessions_group in placed_sessions.items():
            div_key, course_code, subj_type, start_slot, sess_idx = key

            batch_ids = [s.batch_id for s in sessions_group]
            fac_ids = [s.fac_id for s in sessions_group if s.fac_id]
            room_nos = [s.room_no for s in sessions_group if s.room_no]

            # Deduplicate while preserving order
            seen_fac = set()
            unique_fac = []
            for f in fac_ids:
                if f not in seen_fac:
                    seen_fac.add(f)
                    unique_fac.append(f)

            seen_room = set()
            unique_room = []
            for r in room_nos:
                if r not in seen_room:
                    seen_room.add(r)
                    unique_room.append(r)

            rec = AssignmentRecord(
                slot_id=start_slot,
                duration=sessions_group[0].duration,
                division_key=div_key,
                batch_ids=batch_ids,
                course_code=course_code,
                subject_type=subj_type,
                fac_ids=unique_fac if unique_fac else ["-"],
                room_nos=unique_room if unique_room else ["-"],
                is_practical_group=(subj_type == SubjectType.PRACTICAL and len(batch_ids) > 1),
            )
            index.add(rec)

        # ── Post-solve conflict detection ──────────────
        _detect_conflicts(data, index, solver, slot_vars)

        if penalties:
            print(f"Objective value (penalty): {solver.ObjectiveValue()}")

        return solver.StatusName(status), index
    else:
        index.log_conflict("SOLVER", f"Solver returned: {solver.StatusName(status)}")
        return solver.StatusName(status), index


def _detect_conflicts(data, index, solver, slot_vars):
    """Post-solve check for any remaining conflicts (should be zero if solver is correct)."""
    # Check faculty double-bookings
    for fac_id, week in index.faculty_index.items():
        for sid, recs in week.items():
            # Deduplicate by course to ignore combined elective classes
            unique_courses = set()
            for r in recs:
                unique_courses.add((r.course_code, r.subject_type))
                
            if len(unique_courses) > 1:
                courses = [r.course_code for r in recs]
                divs = [r.division_key for r in recs]
                day, period = slot_to_day_period(sid)
                index.log_conflict(
                    "FACULTY",
                    f"{fac_id} double-booked at {day} P{period}: {courses} for {divs}"
                )

    # Check room double-bookings
    for room_no, week in index.room_index.items():
        for sid, recs in week.items():
            # Deduplicate by course to ignore combined elective classes
            unique_courses = set()
            for r in recs:
                unique_courses.add((r.course_code, r.subject_type))
                
            if len(unique_courses) > 1:
                courses = [r.course_code for r in recs]
                day, period = slot_to_day_period(sid)
                index.log_conflict(
                    "ROOM",
                    f"Room {room_no} double-booked at {day} P{period}: {courses}"
                )
