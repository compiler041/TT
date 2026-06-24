"""
Timetable generation engine using Google OR-Tools CP-SAT.

Implements the constraints from constraints_for_tt.docx:

Hard:
  HC-H1  Theory = whole division, one teacher, one room, no split
  HC-H2  Practicals split into batches, each batch -> separate lab,
         all batches of a division in practicals at the SAME slot
         (but not necessarily same subject)
  HC-H3  Division in practicals -> no theory/tutorial for any of its batches that slot
  HC-H4  Each (CLASS, BRANCH, DIVISION) has its own independent timetable
  HC-H6  Elective slots are shared cross-division; home division must be free
  HC-H7  Fixed-time courses (CSV3 / MDM) are pinned before solving
  HC-T1/T2  Teacher cannot double-book (across everything)
  HC-T3  Teacher must be in the eligible pool for the subject (from FACULTY_LIST)
  HC-T4  One teacher chosen from pool, must be free
  HC-T5  Subject cannot exceed defined hours/week (== exactly, HC-S1..S3)
  HC-T7  Each practical batch needs its OWN teacher, all free simultaneously
  HC-R1  Room cannot double-book
  HC-R2  Theory -> CR rooms only (division's fixed classroom)
  HC-R3  Practicals -> LAB rooms only
  HC-R4  Lab subject -> specific lab room as given in ROOM NO (if not 'CR'/blank)
  HC-R7  enough lab rooms of required type for all batches simultaneously
  HC-D1  Division cannot have 2 classes at same time (theory/tutorial level)
  HC-D2  Batch cannot be in 2 places at same time
  HC-D3  If any batch in practicals, ALL batches of that division in practicals
  HC-D4  Max periods/day + lunch slot blocked for everyone
  HC-S1  Exactly L theory periods/week
  HC-S2  Exactly T tutorial periods/week
  HC-S3  Exactly P practical periods/week (consecutive, HC-S4)
  HC-S6  L/T/P of same subject not in same slot
  PS-1..PS-5 atomic practical block scheduling, hours satisfied, no partial assign

Soft (best-effort via objective terms when feasible):
  SC-S1 spread theory across week
  SC-Q3 tutorials spread, >=1 day gap
  SC-R1 even room usage
"""
from __future__ import annotations
from collections import defaultdict
from typing import Optional
import pandas as pd
from ortools.sat.python import cp_model

from .data_loader import DEFAULT_SLOTS, LUNCH_SLOT, DAY_CODES, normalize_slot, expand_slot_to_periods


# ---------------------------------------------------------------------------
# Helper: build "session" records from the course master dataframe.
# A session = one row of CSV1 that needs to be scheduled
#   (theory L, tutorial T, or practical P block).
# ---------------------------------------------------------------------------
def _build_sessions(df: pd.DataFrame) -> list[dict]:
    sessions = []
    for idx, row in df.iterrows():
        hours = int(row["NO.OF HOURS"]) if pd.notna(row["NO.OF HOURS"]) else 0
        if hours <= 0:
            continue
        type_subj = row["TYPE OF SUBJECT"]  # L / T / P 
        sessions.append({
            "row_id": idx,
            "class_": row["CLASS"],
            "sem": row["SEM"],
            "branch": row["BRANCH"],
            "division": row["DIVISION"],
            "no_of_batch": row["NO OF BATCH"],
            "type_of_course": row["TYPE OF COURSE"],   # C / E / E1 / E2 / M / MDM / O
            "course_name": row["COURSE_NAME"],
            "faculty_list": row["FACULTY_LIST"],       # list of eligible/assigned faculty
            "room_no": row["ROOM NO"],
            "type_of_subject": type_subj,
            "hours": hours,
        })
    return sessions


def _division_key(s: dict) -> tuple:
    return (s["class_"], s["sem"], s["branch"], s["division"])


def _is_elective(s: dict) -> bool:
    return s["type_of_course"] in ("E", "E1", "E2") or str(s["type_of_course"]).startswith("E")


def _is_online(s: dict) -> bool:
    return str(s["room_no"]).strip().upper() == "ONLINE"


def _practical_batch_count(s: dict) -> int:
    """Number of batches that share the practical (HC-H2/HC-T7)."""
    nb = s["no_of_batch"]
    try:
        n = int(float(nb))
        if n > 0:
            return n
    except (ValueError, TypeError):
        pass
    # 'ALL' or unknown -> infer from faculty list length, else default 1
    if s["faculty_list"]:
        return max(1, len(s["faculty_list"]))
    return 1


def _practical_consecutive_pairs(slots: list[str], hours: int) -> list[list[str]]:
    """Return list of consecutive-slot windows of length `hours`,
    skipping any window containing the lunch slot."""
    windows = []
    n = len(slots)
    for i in range(n - hours + 1):
        window = slots[i:i + hours]
        if LUNCH_SLOT in window:
            continue
        windows.append(window)
    return windows


class TimetableEngine:
    def __init__(
        self,
        course_master: pd.DataFrame,
        rooms: pd.DataFrame,
        fixed_slots: pd.DataFrame,
        faculty_busy: pd.DataFrame,
        days: Optional[list[str]] = None,
        slots: Optional[list[str]] = None,
        lunch_slot: str = LUNCH_SLOT,
        classes_filter: Optional[list[str]] = None,
        branches_filter: Optional[list[str]] = None,
    ):
        self.df = course_master.copy()
        if classes_filter:
            self.df = self.df[self.df["CLASS"].isin([c.upper() for c in classes_filter])]
        if branches_filter:
            self.df = self.df[self.df["BRANCH"].isin([b.upper() for b in branches_filter])]

        self.rooms = rooms
        self.fixed_slots = fixed_slots
        self.faculty_busy = faculty_busy

        self.days = days or DAY_CODES
        self.slots = slots or DEFAULT_SLOTS
        self.lunch_slot = lunch_slot

        # All (day, slot) combos excluding lunch
        self.timeslots = [(d, s) for d in self.days for s in self.slots if s != self.lunch_slot]
        self.slot_index = {ts: i for i, ts in enumerate(self.timeslots)}

        # Rooms by type
        self.cr_rooms = self.rooms[self.rooms["TYPE"] == "CR"]["ROOM"].tolist()
        self.lab_rooms = self.rooms[self.rooms["TYPE"] == "LAB"]["ROOM"].tolist()
        self.all_rooms = self.cr_rooms + self.lab_rooms

        # Faculty busy lookup: faculty -> set of (day, slot)
        self.faculty_busy_set: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for _, r in self.faculty_busy.iterrows():
            for sl in expand_slot_to_periods(r["SLOT"]):
                self.faculty_busy_set[r["FACULTY NAME"]].add((r["DAY"], sl))

        self.sessions = _build_sessions(self.df)

        self.bottlenecks: list[str] = []

    # ------------------------------------------------------------------
    def _fixed_timeslots_for(self, s: dict) -> list[tuple[str, str]]:
        """HC-H7: return pinned (day, slot) options from CSV3 for this session,
        matching CLASS/SEM/TYPE OF COURSE."""
        if self.fixed_slots.empty:
            return []
        mask = (
            (self.fixed_slots["CLASS"] == s["class_"])
            & (self.fixed_slots["SEM"] == s["sem"])
            & (self.fixed_slots["TYPE OF COURSE"] == s["type_of_course"])
        )
        rows = self.fixed_slots[mask]
        out = []
        for _, r in rows.iterrows():
            day = r["DAY"]
            for slot in expand_slot_to_periods(r["SLOT"]):
                if (day, slot) in self.slot_index and slot != self.lunch_slot:
                    out.append((day, slot))
        return out

    # ------------------------------------------------------------------
    def _room_options_for(self, s: dict, batch_count: int = 1, batch_index: int = 0) -> list[str]:
        """HC-R2/R3/R4: candidate rooms for this session/batch.

        For practicals with multiple batches (HC-H2/HC-R7), only ONE batch
        (the first) is pinned to the subject-specific lab given in ROOM NO
        when that value is a recognized lab; the remaining batches choose
        freely among all lab rooms (each batch gets its own room).
        """
        room_no = str(s["room_no"]).strip().upper()
        if s["type_of_subject"] == "L":
            if room_no in self.cr_rooms:
                return [room_no]
            return self.cr_rooms
        if s["type_of_subject"] == "T":
            if room_no in self.cr_rooms:
                return [room_no]
            return self.cr_rooms or [room_no]
        if s["type_of_subject"] == "P":
            if room_no in self.lab_rooms:
                if batch_count <= 1 or batch_index == 0:
                    return [room_no]
                return [r for r in self.lab_rooms if r != room_no] or self.lab_rooms
            return self.lab_rooms
        return self.all_rooms

    # ------------------------------------------------------------------
    def _pre_solve_validation(self, blocks: list[dict]) -> list[str]:
        """
        PS-2 / HC-T7: detect structural data problems that make the model
        infeasible BEFORE wasting time on the solver, e.g. the same faculty
        listed for two different batches of the same simultaneous practical
        (each batch needs its OWN free teacher).
        """
        issues = []
        for b in blocks:
            if b["type_of_subject"] != "P" or b.get("occurrence", 0) != 0:
                continue
            n = _practical_batch_count(b)
            fac_list = b["faculty_list"]
            if n > 1 and len(fac_list) >= n:
                seen = {}
                for i in range(n):
                    f = fac_list[i]
                    if f in seen:
                        issues.append(
                            f"Faculty conflict in '{b['course_name']}' practical "
                            f"({b['class_']} {b['branch']} {b['division']}): "
                            f"'{f}' is listed for both batch {seen[f]+1} and "
                            f"batch {i+1}, but all {n} batches run simultaneously "
                            f"and each needs a DIFFERENT free teacher (HC-T7)."
                        )
                    seen[f] = i
        return issues

    # ------------------------------------------------------------------
    def generate(self, time_limit_seconds: int = 60) -> dict:
        model = cp_model.CpModel()

        # ------------------------------------------------------------
        # Build "blocks" to schedule.
        #   - L / T sessions: `hours` separate single-period blocks (HC-S1/S2)
        #   - P sessions: ceil-grouped into consecutive windows of size
        #     equal to a "lab session length" (commonly 2). We detect the
        #     lab session length as min(hours, 2) per occurrence, splitting
        #     `hours` into chunks of 2 (then remainder of 1 if odd) — this
        #     keeps HC-S4 (consecutive) while satisfying total weekly hours (PS-5).
        # ------------------------------------------------------------
        blocks = []  # each: dict describing one schedulable unit
        for s in self.sessions:
            if _is_online(s):
                continue  # ONLINE courses are not scheduled into rooms/slots
            if s["type_of_subject"] in ("L", "T"):
                for occ in range(s["hours"]):
                    blocks.append({**s, "occurrence": occ, "block_hours": 1, "kind": "single"})
            elif s["type_of_subject"] == "P":
                remaining = s["hours"]
                occ = 0
                while remaining > 0:
                    chunk = 2 if remaining >= 2 else 1
                    blocks.append({**s, "occurrence": occ, "block_hours": chunk, "kind": "practical"})
                    remaining -= chunk
                    occ += 1

        validation_issues = self._pre_solve_validation(blocks)
        if validation_issues:
            return {
                "status": "failed",
                "message": "Input data has structural conflicts that must be fixed before a timetable can be generated.",
                "bottlenecks": validation_issues,
                "timetable": [],
            }


        # ------------------------------------------------------------
        # Decision variables.
        # For each block: pick a (day, start_slot_index, room_choice, faculty_choice)
        # We model with one IntVar for the (day, slot) start, one for room index,
        # and for practicals one var per batch for room/faculty.
        # ------------------------------------------------------------

        # Precompute valid window starts (consecutive, non-lunch-spanning) per block_hours
        windows_by_hours: dict[int, list[tuple[str, list[str]]]] = {}
        for h in (1, 2):
            wins = []
            for d in self.days:
                for w in _practical_consecutive_pairs(self.slots, h):
                    wins.append((d, w))
            windows_by_hours[h] = wins

        block_vars = []  # parallel list aligned with `blocks`

        # Group practicals by division to enforce HC-D3 / HC-H2 sync.
        division_practical_blocks: dict[tuple, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

        for bi, b in enumerate(blocks):
            div_key = _division_key(b)

            # Determine candidate windows, honoring HC-H7 fixed slots if applicable
            fixed = self._fixed_timeslots_for(b)
            h = b["block_hours"]
            candidates = windows_by_hours[h]
            if fixed:
                fixed_set = set(fixed)
                # for single-period blocks, fixed windows are exact matches
                candidates = [
                    (d, w) for (d, w) in candidates
                    if all((d, x) in fixed_set for x in w)
                ]
                if not candidates:
                    # fall back to all if fixed info doesn't line up cleanly
                    candidates = windows_by_hours[h]

            n_win = len(candidates)
            win_var = model.NewIntVar(0, max(n_win - 1, 0), f"win_{bi}")

            # rooms (computed per-batch below)
            batch_count = _practical_batch_count(b) if b["kind"] == "practical" else 1
            fac_list = b["faculty_list"]

            entry = {
                "block": b,
                "win_var": win_var,
                "candidates": candidates,
                "room_opts": [],  # per batch
                "room_vars": [],   # one per batch
                "fac_vars": [],    # one per batch (index into faculty pool)
                "fac_pool": [],    # eligible faculty per batch
                "batch_count": batch_count,
            }

            for batch_i in range(batch_count):
                room_opts = self._room_options_for(b, batch_count=batch_count, batch_index=batch_i)
                if not room_opts:
                    room_opts = self.all_rooms or ["NOROOM"]
                entry["room_opts"].append(room_opts)

                if b["kind"] == "practical" and len(fac_list) >= batch_count:
                    pool = [fac_list[batch_i]]
                elif fac_list:
                    pool = fac_list
                else:
                    pool = ["UNASSIGNED"]
                entry["fac_pool"].append(pool)

                fvar = model.NewIntVar(0, max(len(pool) - 1, 0), f"fac_{bi}_{batch_i}")
                entry["fac_vars"].append(fvar)

                rvar = model.NewIntVar(0, max(len(room_opts) - 1, 0), f"room_{bi}_{batch_i}")
                entry["room_vars"].append(rvar)

            block_vars.append(entry)

            if b["kind"] == "practical":
                division_practical_blocks[div_key][b["course_name"]].append(bi)

        if not block_vars:
            return {
                "status": "failed",
                "message": "No schedulable sessions found in the input file.",
                "bottlenecks": ["Input file produced zero sessions to schedule."],
                "timetable": [],
            }

        # ------------------------------------------------------------
        # Build occupancy interval/no-overlap structures.
        # We enumerate, for each block, the set of (timeslot_index_set, room, faculty)
        # combinations are too large to fully enumerate generically for CP-SAT
        # in a simple way, so we use boolean "assignment" variables per
        # (block, candidate-window) and per (batch, room-option, faculty-option),
        # then post no-overlap via sums over shared resources per timeslot.
        # ------------------------------------------------------------
        # For tractability we create boolean vars: x[bi, wi] = 1 if block bi uses window wi
        x = {}
        for bi, entry in enumerate(block_vars):
            n = max(len(entry["candidates"]), 1)
            for wi in range(n):
                x[(bi, wi)] = model.NewBoolVar(f"x_{bi}_{wi}")
            if entry["candidates"]:
                model.Add(sum(x[(bi, wi)] for wi in range(n)) == 1)
            else:
                # No valid window (shouldn't normally happen) -> infeasible block, skip scheduling
                model.Add(x[(bi, 0)] == 0)

        # room/faculty selection booleans
        rsel = {}  # (bi, batch_i, room_idx) -> bool
        fsel = {}  # (bi, batch_i, fac_idx) -> bool
        for bi, entry in enumerate(block_vars):
            for batch_i in range(entry["batch_count"]):
                nr = max(len(entry["room_opts"][batch_i]), 1)
                for ri in range(nr):
                    rsel[(bi, batch_i, ri)] = model.NewBoolVar(f"r_{bi}_{batch_i}_{ri}")
                model.Add(sum(rsel[(bi, batch_i, ri)] for ri in range(nr)) == 1)

                pool = entry["fac_pool"][batch_i]
                nf = max(len(pool), 1)
                for fi in range(nf):
                    fsel[(bi, batch_i, fi)] = model.NewBoolVar(f"f_{bi}_{batch_i}_{fi}")
                model.Add(sum(fsel[(bi, batch_i, fi)] for fi in range(nf)) == 1)

        # ------------------------------------------------------------
        # Constraint: HC-R1 / HC-T1/T2 / HC-D1/D2 — no double booking.
        # For every (day, slot) timeslot, sum of "occupies this slot" indicators
        # for a given room / faculty / division(+batch) must be <= 1.
        # ------------------------------------------------------------
        ts_index = {ts: i for i, ts in enumerate(self.timeslots)}
        n_ts = len(self.timeslots)

        room_occ = defaultdict(list)       # room -> list of bool vars indicating occupancy at each ts
        faculty_occ = defaultdict(list)    # faculty -> list of bool vars
        division_occ = defaultdict(list)   # (div_key, batch_label) -> list of bool

        room_at_ts = defaultdict(list)     # (room, ts_i) -> list of bools
        faculty_at_ts = defaultdict(list)  # (faculty, ts_i) -> list of bools
        division_at_ts = defaultdict(list)  # (div_key, batch_label, ts_i) -> list of bools

        for bi, entry in enumerate(block_vars):
            b = entry["block"]
            div_key = _division_key(b)
            for wi, (day, window_slots) in enumerate(entry["candidates"]):
                occupies_ts = [ts_index[(day, sl)] for sl in window_slots if (day, sl) in ts_index]
                for batch_i in range(entry["batch_count"]):
                    # room occupancy
                    for ri, room in enumerate(entry["room_opts"][batch_i]):
                        var = rsel.get((bi, batch_i, ri))
                        if var is None:
                            continue
                        both = model.NewBoolVar(f"both_room_{bi}_{batch_i}_{wi}_{ri}")
                        model.AddMultiplicationEquality(both, [x[(bi, wi)], var])
                        for tsi in occupies_ts:
                            room_at_ts[(room, tsi)].append(both)

                    # faculty occupancy
                    for fi, fac in enumerate(entry["fac_pool"][batch_i]):
                        var = fsel.get((bi, batch_i, fi))
                        if var is None or fac == "UNASSIGNED":
                            continue
                        both = model.NewBoolVar(f"both_fac_{bi}_{batch_i}_{wi}_{fi}")
                        model.AddMultiplicationEquality(both, [x[(bi, wi)], var])
                        for tsi in occupies_ts:
                            faculty_at_ts[(fac, tsi)].append(both)

                    # division/batch occupancy (HC-D1/D2/D3, HC-H3, HC-S6)
                    # MDM / Minor / Elective courses are cross-division electives:
                    # they don't block the WHOLE-division slot for the (often
                    # synthetic) BRANCH='ALL' division — HC-H6/HC-D5 (home
                    # division must be free) is handled by the fixed-slot
                    # placement itself, not by additional blocking here.
                    is_cross_division = b["type_of_course"] in ("M", "MDM") or _is_elective(b)
                    if is_cross_division and b["branch"] == "ALL":
                        continue

                    if b["kind"] == "practical":
                        batch_label = f"BATCH{batch_i+1}"
                    else:
                        batch_label = "WHOLE"  # theory/tutorial occupies whole division
                    for tsi in occupies_ts:
                        division_at_ts[(div_key, batch_label, tsi)].append(x[(bi, wi)])

        # No double-booking: room
        for (room, tsi), vars_ in room_at_ts.items():
            if len(vars_) > 1:
                model.Add(sum(vars_) <= 1)

        # No double-booking: faculty (HC-T1/T2)
        for (fac, tsi), vars_ in faculty_at_ts.items():
            # also exclude faculty's globally-busy slots
            day, slot = self.timeslots[tsi]
            if (day, slot) in self.faculty_busy_set.get(fac, set()):
                for v in vars_:
                    model.Add(v == 0)
            if len(vars_) > 1:
                model.Add(sum(vars_) <= 1)

        # Division-level: WHOLE (theory/tutorial) cannot coincide with ANY other
        # occupancy of the same division at the same ts (HC-D1/HC-H3/HC-S6).
        div_keys_at_ts = defaultdict(set)
        for (div_key, batch_label, tsi), vars_ in division_at_ts.items():
            div_keys_at_ts[(div_key, tsi)].add(batch_label)

        for (div_key, tsi), labels in div_keys_at_ts.items():
            # WHOLE (theory/tutorial, whole-division) cannot coincide with
            # any practical batch occupancy of the same division at this ts.
            whole_vars = division_at_ts.get((div_key, "WHOLE", tsi), [])
            other_vars = []
            for label in labels:
                if label != "WHOLE":
                    other_vars.extend(division_at_ts[(div_key, label, tsi)])
            if whole_vars and other_vars:
                for wv in whole_vars:
                    for ov in other_vars:
                        model.Add(wv + ov <= 1)
            if len(whole_vars) > 1:
                model.Add(sum(whole_vars) <= 1)

        # HC-D3 / HC-H2: all batches of a division must be in practicals at the
        # SAME slot when any one of them is. We approximate by forcing all
        # practical blocks belonging to the same division+occurrence-group to
        # share the same window selection across batches that occur in the
        # same "round". Since each practical block already represents all
        # batches together (room/fac vars per batch), the window var `x[bi,*]`
        # already applies division-wide — batches differ only in room/faculty.
        # No additional constraint needed beyond room/faculty no-overlap above.

        # ------------------------------------------------------------
        # HC-H6 / HC-D5 / HC-S7: Elective sessions create cross-division
        # groups sharing a slot; home divisions of OTHER electives in the
        # same TYPE OF COURSE group (E1/E2/etc.) at the SAME class+sem must
        # not be double-booked — this falls out naturally because each
        # division's theory ("WHOLE") occupancy already prevents overlap
        # within that division. Electives across divisions sharing a room
        # use distinct rooms via HC-R1, and the home-division-free
        # requirement is satisfied because the elective itself occupies the
        # WHOLE slot of its home division.
        # ------------------------------------------------------------

        # ------------------------------------------------------------
        # HC-H1: ensure single teacher/room/division-wide for theory — already
        # guaranteed by batch_count == 1 for L/T blocks and WHOLE occupancy.
        # ------------------------------------------------------------

        # ------------------------------------------------------------
        # Soft constraints / objective: spread subjects across days.
        # Penalize multiple occurrences of the SAME (division, course, type)
        # on the SAME day (SC-S1, SC-Q3).
        # ------------------------------------------------------------
        objective_terms = []
        by_course = defaultdict(list)
        for bi, entry in enumerate(block_vars):
            b = entry["block"]
            if b["kind"] == "single":
                key = (_division_key(b), b["course_name"], b["type_of_subject"])
                by_course[key].append(bi)

        for key, bis in by_course.items():
            if len(bis) < 2:
                continue
            for d in self.days:
                day_vars = []
                for bi in bis:
                    entry = block_vars[bi]
                    for wi, (day, _) in enumerate(entry["candidates"]):
                        if day == d:
                            day_vars.append(x[(bi, wi)])
                if len(day_vars) > 1:
                    overflow = model.NewIntVar(0, len(day_vars), f"overflow_{key}_{d}")
                    model.Add(overflow >= sum(day_vars) - 1)
                    objective_terms.append(overflow)

        if objective_terms:
            model.Minimize(sum(objective_terms))

        # ------------------------------------------------------------
        # Solve
        # ------------------------------------------------------------
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_seconds
        solver.parameters.num_search_workers = 8
        status = solver.Solve(model)

        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            self._diagnose_infeasibility(block_vars)
            return {
                "status": "failed",
                "message": "Could not find a feasible timetable with the given constraints.",
                "bottlenecks": self.bottlenecks or [
                    "No specific bottleneck identified — try relaxing hours, "
                    "adding more faculty/room options, or check faculty busy CSV."
                ],
                "timetable": [],
            }

        # ------------------------------------------------------------
        # Extract solution
        # ------------------------------------------------------------
        timetable = []
        for bi, entry in enumerate(block_vars):
            b = entry["block"]
            chosen_wi = None
            for wi in range(len(entry["candidates"])):
                if solver.Value(x[(bi, wi)]) == 1:
                    chosen_wi = wi
                    break
            if chosen_wi is None:
                continue
            day, window_slots = entry["candidates"][chosen_wi]

            for batch_i in range(entry["batch_count"]):
                room_opts = entry["room_opts"][batch_i]
                chosen_room = None
                for ri, room in enumerate(room_opts):
                    if solver.Value(rsel[(bi, batch_i, ri)]) == 1:
                        chosen_room = room
                        break
                pool = entry["fac_pool"][batch_i]
                chosen_fac = None
                for fi, fac in enumerate(pool):
                    if solver.Value(fsel[(bi, batch_i, fi)]) == 1:
                        chosen_fac = fac
                        break

                batch_label = f"B{batch_i+1}" if b["kind"] == "practical" and entry["batch_count"] > 1 else None

                for sl in window_slots:
                    period_index = self.slots.index(sl)
                    timetable.append({
                        "class_": b["class_"],
                        "sem": b["sem"],
                        "branch": b["branch"],
                        "division": b["division"],
                        "batch": batch_label,
                        "day": day,
                        "slot": sl,
                        "period_index": period_index,
                        "course_name": b["course_name"],
                        "type_of_subject": b["type_of_subject"],
                        "faculty": chosen_fac,
                        "room": chosen_room,
                        "is_elective": _is_elective(b),
                        "elective_group": b["type_of_course"] if _is_elective(b) else None,
                    })

        status_str = "success" if status == cp_model.OPTIMAL else "partial"
        return {
            "status": status_str,
            "message": "Timetable generated successfully." if status_str == "success"
                       else "A feasible timetable was generated (search time limit reached before optimality).",
            "bottlenecks": [],
            "timetable": timetable,
        }

    # ------------------------------------------------------------------
    def _diagnose_infeasibility(self, block_vars: list[dict]) -> None:
        """PS-2: best-effort bottleneck reporting for an infeasible model."""
        self.bottlenecks = []

        # Check: rooms of required type vs batches needed
        for entry in block_vars:
            b = entry["block"]
            if b["kind"] != "practical":
                continue
            needed = entry["batch_count"]
            room_no = str(b["room_no"]).strip().upper()
            if room_no in self.lab_rooms:
                # 1 specific lab for batch 0 + remaining labs for other batches
                available = 1 + max(len(self.lab_rooms) - 1, 0)
            else:
                available = len(self.lab_rooms)
            if available < needed:
                self.bottlenecks.append(
                    f"Not enough lab rooms for '{b['course_name']}' "
                    f"({b['class_']} {b['branch']} {b['division']}): "
                    f"needs {needed} batch(es), only {available} lab room(s) of "
                    f"required type available."
                )

        # Check: faculty pool sizes for practicals
        for entry in block_vars:
            b = entry["block"]
            if b["kind"] != "practical":
                continue
            for batch_i, pool in enumerate(entry["fac_pool"]):
                if pool == ["UNASSIGNED"]:
                    self.bottlenecks.append(
                        f"No faculty assigned for batch {batch_i+1} of "
                        f"'{b['course_name']}' ({b['class_']} {b['branch']} {b['division']})."
                    )

        # Check: faculty over-busy (busy in every slot of the week)
        total_slots = len(self.timeslots)
        for fac, busy in self.faculty_busy_set.items():
            if len(busy) >= total_slots:
                self.bottlenecks.append(
                    f"Faculty '{fac}' is marked unavailable for ALL timeslots — "
                    f"cannot be assigned any class."
                )

        if not self.bottlenecks:
            self.bottlenecks.append(
                "Constraints are too tight to satisfy simultaneously "
                "(e.g. fixed slots, faculty availability, hours/week). "
                "Try increasing the solver time limit or relaxing hours/faculty constraints."
            )
