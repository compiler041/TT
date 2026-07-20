"""
Scheduler - the brain that places every subject into the timetable.
"""

import random
from itertools import groupby
from app.models import (
    Timetable, ScheduleEntry, TimeSlot, Subject, Classroom,
    FixedSlot, FacultyUnavailability, VALID_DAYS, VALID_PERIODS,
    DivisionMeta, PracticalSession
)
from app.constraints import is_valid_placement_v2, room_is_free, score_placement

ONLINE_ROOM = Classroom(room_id="ONLINE", room_type="ONLINE")
ELECTIVE_TYPES = {"E", "E1", "E2", "O", "MDM", "M"}
CORE_TYPES = {"C"}

def all_slots(duration: int = 1) -> list[TimeSlot]:
    slots = []
    for d in VALID_DAYS:
        for p in VALID_PERIODS:
            try:
                slots.append(TimeSlot(day=d, start_period=p, duration=duration))
            except ValueError:
                pass
    return slots

def resolve_classroom(
    subject: Subject,
    classrooms: list[Classroom],
    timetable: Timetable,
    slot: TimeSlot,
    division_meta: dict[tuple[str, str, str], DivisionMeta] = None,
    claimed_rooms: set[str] = None,
    is_elective: bool = False
) -> Classroom | None:
    if claimed_rooms is None: claimed_rooms = set()
    room_label = subject.room.strip().upper()

    if room_label == "ONLINE":
        return ONLINE_ROOM

    if room_label != "CR":
        for classroom in classrooms:
            if classroom.room_id == room_label:
                if classroom.room_id not in claimed_rooms and room_is_free(timetable, classroom.room_id, slot):
                    return classroom
                # If specific room is busy, and it's a practical, we will fallback to finding ANY lab.
                if not subject.is_practical:
                    return None
                break
        else:
            # If we didn't find the room_label in classrooms at all
            print(f"[WARN] Room '{subject.room}' not in room list for '{subject.name}'")
            if not subject.is_practical:
                return None

    # CR: find free room
    # If it's theory and NOT an elective, we should use the division's fixed room
    # Electives need multiple rooms because they run in parallel
    if not subject.is_practical and subject.subject_type == "L" and not is_elective:
        fixed_room_id = None
        if division_meta and subject.class_key in division_meta:
            fixed_room_id = division_meta[subject.class_key].theory_room
            
        if not fixed_room_id:
            # Dynamically check if this division already has a room assigned in the timetable
            for e in timetable.entries:
                if (e.subject.class_key == subject.class_key 
                    and not e.subject.is_practical 
                    and e.classroom.room_type.upper() != "ONLINE"
                    and e.subject.course_type not in ELECTIVE_TYPES):
                    fixed_room_id = e.classroom.room_id
                    break
                    
        if fixed_room_id:
            for classroom in classrooms:
                if classroom.room_id == fixed_room_id:
                    if classroom.room_id not in claimed_rooms and room_is_free(timetable, classroom.room_id, slot):
                        return classroom
                    return None

    # Fallback to finding any free room of right type
    need_lab = subject.is_practical
    for classroom in classrooms:
        correct_type = classroom.is_lab if need_lab else not classroom.is_lab
        if correct_type and classroom.room_id not in claimed_rooms and room_is_free(timetable, classroom.room_id, slot):
            return classroom

    return None

# ------------------------------------------------------------------
# Phase 1: Fixed slots (MDM)
# ------------------------------------------------------------------

def place_fixed_slots(
    timetable: Timetable,
    fixed_slots: list[FixedSlot],
    subjects: list[Subject],
    classrooms: list[Classroom],
    division_meta: dict[tuple[str, str, str], DivisionMeta]
) -> list[Subject]:
    placed_subjects = set()

    # Sort fixed slots by period so we place them in order (helps contiguity)
    sorted_fixed = sorted(fixed_slots, key=lambda fs: fs.period)

    for fs in sorted_fixed:
        slot = TimeSlot(day=fs.day, start_period=fs.period, duration=1)

        matches = [
            s for s in subjects
            if s.year == fs.year
            and s.course_type == fs.course_type
            and s.semester == fs.semester
            and (s.subject_type == "L" or s.subject_type == "P")
        ]

        if not matches:
            continue

        for subj in matches:
            faculty_code = subj.faculty_codes[0] if subj.faculty_codes else ""
            classroom = resolve_classroom(subj, classrooms, timetable, slot, division_meta)

            if classroom is None:
                continue

            # Fixed slots are mandatory — place them even if they create gaps.
            # The defrag pass will try to close any gaps afterwards.
            entry = ScheduleEntry(
                subject=subj,
                faculty_code=faculty_code,
                classroom=classroom,
                timeslot=slot,
                batch=subj.batch,
            )
            timetable.add(entry)
            placed_subjects.add(id(subj))

    return [s for s in subjects if id(s) not in placed_subjects]

# ------------------------------------------------------------------
# Phase 2: Elective groups (E, E1, E2, O)
# ------------------------------------------------------------------

def place_elective_groups(
    timetable: Timetable,
    subjects: list[Subject],
    classrooms: list[Classroom],
    faculty_unavailability: list[FacultyUnavailability],
    division_meta: dict[tuple[str, str, str], DivisionMeta]
) -> list[Subject]:
    elective_subjects = [s for s in subjects if s.course_type in ELECTIVE_TYPES and s.subject_type == "L"]
    other_subjects = [s for s in subjects if id(s) not in {id(e) for e in elective_subjects}]

    def group_key(s: Subject):
        return (s.year, s.semester, s.course_type)

    sorted_electives = sorted(elective_subjects, key=group_key)
    unscheduled_electives: list[Subject] = []

    for key, group_iter in groupby(sorted_electives, key=group_key):
        group = list(group_iter)
        slots_needed = int(max(s.hours_per_week for s in group))

        for _ in range(slots_needed):
            placed_this_round = False
            
            # Score slots and pick best
            candidate_slots = []
            for slot in all_slots(duration=1):
                candidate_placements = []
                all_fit = True
                claimed_rooms = set()
                faculty_to_room = {}
                slot_score = 0

                for subj in group:
                    faculty_code = subj.faculty_codes[0] if subj.faculty_codes else ""
                    if faculty_code and faculty_code in faculty_to_room:
                        classroom = faculty_to_room[faculty_code]
                    else:
                        classroom = resolve_classroom(subj, classrooms, timetable, slot, division_meta, claimed_rooms, is_elective=True)
                        if classroom is None:
                            print(f"[DEBUG] Elective {subj.name} ({subj.branch}) failed room at {slot.day} P{slot.start_period}")
                            all_fit = False
                            break
                        if faculty_code:
                            faculty_to_room[faculty_code] = classroom
                        claimed_rooms.add(classroom.room_id)

                    if not is_valid_placement_v2(
                        timetable, subj, faculty_code, classroom, slot, faculty_unavailability
                    ):
                        print(f"[DEBUG] Elective {subj.name} ({subj.branch}) failed is_valid at {slot.day} P{slot.start_period} with room {classroom.room_id}")
                        all_fit = False
                        break

                    candidate_placements.append((subj, faculty_code, classroom))
                    slot_score += score_placement(timetable, subj, faculty_code, slot, classroom)

                if all_fit and len(candidate_placements) == len(group):
                    candidate_slots.append((slot_score, slot, candidate_placements))
                    
            if candidate_slots:
                random.shuffle(candidate_slots)
                candidate_slots.sort(key=lambda x: x[0])
                _, best_slot, best_placements = candidate_slots[0]
                
                for subj, faculty_code, classroom in best_placements:
                    timetable.add(ScheduleEntry(
                        subject=subj,
                        faculty_code=faculty_code,
                        classroom=classroom,
                        timeslot=best_slot,
                        batch=subj.batch,
                    ))
                placed_this_round = True

            if not placed_this_round:
                unscheduled_electives.extend(group)
                break

    return other_subjects + unscheduled_electives

# ------------------------------------------------------------------
# Phase 3a: Practicals
# ------------------------------------------------------------------

def schedule_practicals(
    timetable: Timetable,
    practical_sessions: list[PracticalSession],
    classrooms: list[Classroom],
    faculty_unavailability: list[FacultyUnavailability],
    division_meta: dict[tuple[str, str, str], DivisionMeta],
    bottlenecks: list[dict]
) -> list[Subject]:
    unscheduled = []
    
    # Sort: divisions with more batches first
    def group_key(s: PracticalSession):
        return s.division_key
        
    sorted_sessions = sorted(practical_sessions, key=group_key)
    sessions_by_div = {}
    for k, g in groupby(sorted_sessions, key=group_key):
        sessions_by_div[k] = list(g)
        
    sorted_divs = sorted(sessions_by_div.keys(), key=lambda k: -len(sessions_by_div[k][0].assignments) if sessions_by_div[k] else 0)

    for div_key in sorted_divs:
        sessions = sessions_by_div[div_key]
        
        for session in sessions:
            placed = False
            slot_failures = []
            
            # Score slots
            candidate_slots = []
            for slot in all_slots(duration=2):
                all_ok = True
                resolved = []
                claimed_rooms = set()
                faculty_to_room = {}
                slot_score = 0
                
                for subject, faculty_code in session.assignments:
                    if faculty_code and faculty_code in faculty_to_room:
                        classroom = faculty_to_room[faculty_code]
                    else:
                        classroom = resolve_classroom(subject, classrooms, timetable, slot, division_meta, claimed_rooms)
                        if classroom is None:
                            all_ok = False
                            slot_failures.append((slot, f"Room for {subject.name} unavailable"))
                            break
                        if faculty_code:
                            faculty_to_room[faculty_code] = classroom
                        claimed_rooms.add(classroom.room_id)
                        
                    if not is_valid_placement_v2(
                        timetable, subject, faculty_code, classroom, slot, faculty_unavailability
                    ):
                        all_ok = False
                        slot_failures.append((slot, f"Constraint failed for {subject.name} batch {subject.batch}"))
                        break
                        
                    resolved.append((subject, faculty_code, classroom))
                    slot_score += score_placement(timetable, subject, faculty_code, slot, classroom)
                    
                if all_ok and len(resolved) == len(session.assignments):
                    candidate_slots.append((slot_score, slot, resolved))
                    
            if candidate_slots:
                random.shuffle(candidate_slots)
                candidate_slots.sort(key=lambda x: x[0])
                _, best_slot, best_resolved = candidate_slots[0]
                
                for subject, faculty_code, classroom in best_resolved:
                    timetable.add(ScheduleEntry(
                        subject=subject,
                        faculty_code=faculty_code,
                        classroom=classroom,
                        timeslot=best_slot,
                        batch=subject.batch,
                    ))
                placed = True
                
            if not placed:
                unscheduled.extend([s for s, _ in session.assignments])
                b_name = " + ".join([s.name for s, _ in session.assignments])
                bottlenecks.append({
                    "division": f"{div_key[0]} {div_key[1]} {div_key[2]}",
                    "session": f"Practical Session ({b_name})",
                    "failures": slot_failures[:5] # just keep a few
                })
                
    return unscheduled

# ------------------------------------------------------------------
# Phase 3b: Tutorials
# ------------------------------------------------------------------

def schedule_tutorials(
    timetable: Timetable,
    tutorial_sessions: list[PracticalSession],
    classrooms: list[Classroom],
    faculty_unavailability: list[FacultyUnavailability],
    division_meta: dict[tuple[str, str, str], DivisionMeta],
    bottlenecks: list[dict]
) -> list[Subject]:
    unscheduled = []
    
    def group_key(s: PracticalSession):
        return s.division_key
        
    sorted_sessions = sorted(tutorial_sessions, key=group_key)
    sessions_by_div = {}
    for k, g in groupby(sorted_sessions, key=group_key):
        sessions_by_div[k] = list(g)
        
    for div_key, sessions in sessions_by_div.items():
        for session in sessions:
            placed = False
            slot_failures = []
            
            candidate_slots = []
            for slot in all_slots(duration=1):
                all_ok = True
                resolved = []
                claimed_rooms = set()
                claimed_faculty = set()
                faculty_to_room = {}
                slot_score = 0
                
                for subject, faculty_code in session.assignments:
                    if faculty_code and faculty_code in faculty_to_room:
                        classroom = faculty_to_room[faculty_code]
                    else:
                        classroom = resolve_classroom(subject, classrooms, timetable, slot, division_meta, claimed_rooms)
                        if classroom is None:
                            all_ok = False
                            slot_failures.append((slot, f"Room for {subject.name} unavailable"))
                            break
                        if faculty_code:
                            faculty_to_room[faculty_code] = classroom
                        claimed_rooms.add(classroom.room_id)
                        
                    if not is_valid_placement_v2(
                        timetable, subject, faculty_code, classroom, slot, faculty_unavailability
                    ):
                        all_ok = False
                        slot_failures.append((slot, f"Constraint failed for {subject.name} batch {subject.batch}"))
                        break
                        
                    resolved.append((subject, faculty_code, classroom))
                    slot_score += score_placement(timetable, subject, faculty_code, slot, classroom)
                    
                if all_ok and len(resolved) == len(session.assignments):
                    candidate_slots.append((slot_score, slot, resolved))
                    
            if candidate_slots:
                candidate_slots.sort(key=lambda x: x[0])
                _, best_slot, best_resolved = candidate_slots[0]
                
                for subject, faculty_code, classroom in best_resolved:
                    timetable.add(ScheduleEntry(
                        subject=subject,
                        faculty_code=faculty_code,
                        classroom=classroom,
                        timeslot=best_slot,
                        batch=subject.batch,
                    ))
                placed = True
                
            if not placed:
                unscheduled.extend([s for s, _ in session.assignments])
                b_name = " + ".join([s.name for s, _ in session.assignments])
                bottlenecks.append({
                    "division": f"{div_key[0]} {div_key[1]} {div_key[2]}",
                    "session": f"Tutorial Session ({b_name})",
                    "failures": []
                })
                
    return unscheduled

# ------------------------------------------------------------------
# Phase 3c: Theory
# ------------------------------------------------------------------

def schedule_theory(
    timetable: Timetable,
    subjects: list[Subject],
    classrooms: list[Classroom],
    faculty_unavailability: list[FacultyUnavailability],
    division_meta: dict[tuple[str, str, str], DivisionMeta]
) -> list[Subject]:
    unscheduled = []
    days = ["M", "T", "W", "R", "F"]
    
    from collections import defaultdict
    import math

    # Pre-calculate total weekly hours per division (for score_placement)
    div_weekly_hours = defaultdict(int)
    for s in subjects:
        div_weekly_hours[s.class_key] += s.hours_per_week
    counted_periods = set()
    for e in timetable.entries:
        for p in e.timeslot.occupied_periods:
            key = (e.subject.class_key, e.timeslot.day, p)
            if key not in counted_periods:
                counted_periods.add(key)
                div_weekly_hours[e.subject.class_key] += 1

    def get_div_hours_on_day(class_key, day):
        """Count how many periods a division already has on a day."""
        periods = set()
        for e in timetable.get_division_entries_on_day(class_key, day):
            periods.update(e.timeslot.occupied_periods)
        return len(periods)

    def get_contiguous_periods(class_key, day, batch="ALL"):
        """Get the set of periods that would keep the schedule gap-free on this day
        for the given batch.  A batch sees ALL entries plus its own batch entries.
        Returns periods adjacent to the existing block, or all periods if day is empty."""
        day_entries = timetable.get_division_entries_on_day(class_key, day)
        if not day_entries:
            return set(VALID_PERIODS)  # Empty day — any period is fine

        # Determine which batches we need to keep gap-free
        numbered_batches = set()
        for e in day_entries:
            if e.batch != "ALL":
                numbered_batches.add(e.batch)
        if batch != "ALL":
            numbered_batches.add(batch)

        check_batches = numbered_batches if numbered_batches else {"ALL"}

        # For every batch that this placement could affect,
        # find valid adjacent periods that won't create a gap.
        valid = set(VALID_PERIODS)   # start with all, then intersect
        for b in check_batches:
            b_periods = set()
            for e in day_entries:
                if e.batch == "ALL" or e.batch == b:
                    b_periods.update(e.timeslot.occupied_periods)

            if not b_periods:
                # This batch has nothing scheduled today — any period is ok
                continue

            # Only allow periods adjacent to the existing contiguous block
            min_p = min(b_periods)
            max_p = max(b_periods)
            b_valid = set()
            if min_p - 1 >= 1:
                b_valid.add(min_p - 1)
            if max_p + 1 <= 9:
                b_valid.add(max_p + 1)
            # Also allow periods inside the block that aren't occupied
            # (in case another batch left a hole this batch can fill)
            for p in range(min_p, max_p + 1):
                if p not in b_periods:
                    b_valid.add(p)
            b_valid -= b_periods   # don't re-occupy
            valid &= b_valid

        # Also remove already-occupied periods for ALL batches
        all_occupied = set()
        for e in day_entries:
            all_occupied.update(e.timeslot.occupied_periods)
        valid -= all_occupied
        return valid

    # Build work list: (subject, faculty_code, hours_remaining)
    work_items = []
    for subj in subjects:
        faculty_code = subj.faculty_codes[0] if subj.faculty_codes else ""
        hours_missing = int(subj.hours_per_week) - timetable.count_subject_hours(subj)
        if hours_missing > 0:
            work_items.append({
                'subject': subj,
                'faculty': faculty_code,
                'remaining': hours_missing,
            })

    # Sort by most constrained first (tightest divisions first)
    work_items.sort(key=lambda w: (
        -div_weekly_hours[w['subject'].class_key],
        -w['remaining'],
        random.random()
    ))

    # Round-robin: place ONE hour of each subject per round
    max_rounds = max((w['remaining'] for w in work_items), default=0)
    
    for round_num in range(int(max_rounds)):
        # Shuffle within each round for variety across attempts
        round_items = [w for w in work_items if w['remaining'] > 0]
        random.shuffle(round_items)
        # But keep most-constrained divisions first
        round_items.sort(key=lambda w: (
            -div_weekly_hours[w['subject'].class_key],
            random.random()
        ))

        for item in round_items:
            if item['remaining'] <= 0:
                continue

            subj = item['subject']
            faculty_code = item['faculty']

            # Sort days by fewest hours for this division (balance load)
            day_order = sorted(days, key=lambda d: (
                get_div_hours_on_day(subj.class_key, d),
                random.random()
            ))

            placed = False
            for day in day_order:
                # Get contiguous-only periods for this day (batch-aware)
                valid_periods = get_contiguous_periods(subj.class_key, day, subj.batch)
                if not valid_periods:
                    continue

                candidate_slots = []
                for period in sorted(valid_periods):
                    try:
                        slot = TimeSlot(day=day, start_period=period, duration=subj.duration)
                    except ValueError:
                        continue

                    classroom = resolve_classroom(subj, classrooms, timetable, slot, division_meta)
                    if classroom is None:
                        continue

                    if is_valid_placement_v2(
                        timetable, subj, faculty_code, classroom, slot, faculty_unavailability
                    ):
                        score = score_placement(timetable, subj, faculty_code, slot, classroom, division_weekly_hours=div_weekly_hours)
                        candidate_slots.append((score, slot, classroom))

                if candidate_slots:
                    candidate_slots.sort(key=lambda x: x[0])
                    _, best_slot, best_classroom = candidate_slots[0]
                    
                    timetable.add(ScheduleEntry(
                        subject=subj,
                        faculty_code=faculty_code,
                        classroom=best_classroom,
                        timeslot=best_slot,
                        batch=subj.batch,
                    ))
                    item['remaining'] -= subj.duration
                    placed = True
                    break  # Move to next subject in round-robin

            if not placed:
                # Fallback: try ALL valid slots, but is_valid_placement_v2
                # will still enforce the hard no-gap constraint.
                fallback_slots = []
                for slot in all_slots(duration=subj.duration):
                    classroom = resolve_classroom(subj, classrooms, timetable, slot, division_meta)
                    if classroom is None:
                        continue
                    if is_valid_placement_v2(
                        timetable, subj, faculty_code, classroom, slot, faculty_unavailability
                    ):
                        score = score_placement(timetable, subj, faculty_code, slot, classroom, division_weekly_hours=div_weekly_hours)
                        fallback_slots.append((score, slot, classroom))
                
                if fallback_slots:
                    fallback_slots.sort(key=lambda x: x[0])
                    _, best_slot, best_classroom = fallback_slots[0]
                    timetable.add(ScheduleEntry(
                        subject=subj,
                        faculty_code=faculty_code,
                        classroom=best_classroom,
                        timeslot=best_slot,
                        batch=subj.batch,
                    ))
                    item['remaining'] -= subj.duration

    # Collect unscheduled subjects
    for item in work_items:
        if item['remaining'] > 0:
            print(f"[DEBUG] Core Theory {item['subject'].name} ({item['subject'].branch}) FAILED TO PLACE! remaining={item['remaining']}")
            unscheduled.append(item['subject'])

    return unscheduled

# ------------------------------------------------------------------
# Post-processing: Defragmentation (close gaps)
# ------------------------------------------------------------------

def defrag_timetable(timetable: Timetable, faculty_unavailability: list[FacultyUnavailability]) -> int:
    """
    Aggressive post-processing: close gaps in each division+batch's daily schedule.
    Works per-batch: for each batch on each day, compute the batch's view 
    (ALL entries + batch-specific entries) and compact them into one block.
    """
    total_moves = 0
    days = ["M", "T", "W", "R", "F"]

    div_keys = set()
    for e in timetable.entries:
        div_keys.add(e.subject.class_key)

    def can_move_entry(entry, new_period):
        """Check if an entry can safely move to new_period on the same day."""
        try:
            new_slot = TimeSlot(day=entry.timeslot.day, start_period=new_period, duration=entry.timeslot.duration)
        except ValueError:
            return False
        
        # Check all periods the new slot would occupy are valid
        for p in new_slot.occupied_periods:
            if p not in VALID_PERIODS:
                return False

        # Faculty free? (exclude self)
        for e in timetable.entries:
            if e is entry:
                continue
            if e.faculty_code == entry.faculty_code and e.timeslot.overlaps(new_slot):
                return False

        # Faculty unavailability
        for p in new_slot.occupied_periods:
            for u in faculty_unavailability:
                if u.faculty_code == entry.faculty_code and u.day == entry.timeslot.day and u.period == p:
                    return False

        # Room free? (exclude self, skip ONLINE)
        if entry.classroom.room_id != "ONLINE":
            for e in timetable.entries:
                if e is entry:
                    continue
                if e.classroom.room_id == entry.classroom.room_id and e.timeslot.overlaps(new_slot):
                    return False

        # Class/batch free? (exclude self)
        for e in timetable.entries:
            if e is entry:
                continue
            if not e.timeslot.overlaps(new_slot):
                continue
            e_year, e_branch, e_div = e.subject.class_key
            q_year, q_branch, q_div = entry.subject.class_key
            if e_year != q_year or e_div != q_div:
                continue
            if e_branch == q_branch or e_branch == "ALL" or q_branch == "ALL":
                if e.batch == "ALL" or entry.batch == "ALL" or e.batch == entry.batch:
                    return False

        return True

    def move_entry(entry, new_period):
        """Move an entry to a new period. Returns the new entry."""
        new_slot = TimeSlot(day=entry.timeslot.day, start_period=new_period, duration=entry.timeslot.duration)
        new_entry = ScheduleEntry(
            subject=entry.subject,
            faculty_code=entry.faculty_code,
            classroom=entry.classroom,
            timeslot=new_slot,
            batch=entry.batch,
        )
        # Find by identity (id), not by value — safe even after prior moves
        for i, e in enumerate(timetable.entries):
            if e is entry:
                timetable.entries[i] = new_entry
                return new_entry
        # Fallback: try by value match
        for i, e in enumerate(timetable.entries):
            if (e.subject is entry.subject and e.faculty_code == entry.faculty_code 
                and e.timeslot.day == entry.timeslot.day 
                and e.timeslot.start_period == entry.timeslot.start_period
                and e.batch == entry.batch):
                timetable.entries[i] = new_entry
                return new_entry
        print(f"[DEFRAG-WARN] Could not find entry to move: {entry.subject.name} on {entry.timeslot.day} P{entry.timeslot.start_period}")
        return entry  # Return unchanged if not found

    def has_gaps(periods):
        if len(periods) <= 1:
            return False
        ps = sorted(periods)
        for i in range(1, len(ps)):
            if ps[i] - ps[i-1] > 1:
                return True
        return False

    # Run multiple passes (gaps closed in one pass may enable closing another)
    for pass_num in range(10):
        pass_moves = 0

        for div_key in div_keys:
            for day in days:
                day_entries = timetable.get_division_entries_on_day(div_key, day)
                if len(day_entries) <= 1:
                    continue

                # Determine batches present
                batches = set()
                for e in day_entries:
                    batches.add(e.batch)
                numbered = {b for b in batches if b != "ALL"}
                check_batches = numbered if numbered else {"ALL"}

                for batch in check_batches:
                    # Get this batch's view (ALL + batch-specific)
                    batch_entries = [e for e in day_entries if e.batch == "ALL" or e.batch == batch]
                    batch_periods = set()
                    for e in batch_entries:
                        batch_periods.update(e.timeslot.occupied_periods)

                    if not has_gaps(batch_periods):
                        continue

                    # Separate movable theory from locked practicals
                    theory_entries = [e for e in batch_entries if e.timeslot.duration == 1]
                    practical_periods = set()
                    for e in batch_entries:
                        if e.timeslot.duration >= 2:
                            practical_periods.update(e.timeslot.occupied_periods)

                    if not theory_entries:
                        continue

                    # Compact towards the anchor (min occupied period)
                    anchor = min(batch_periods)
                    target = anchor
                    theory_sorted = sorted(theory_entries, key=lambda e: e.timeslot.start_period)
                    
                    for entry in theory_sorted:
                        while target in practical_periods and target <= 9:
                            target += 1
                        if target > 9:
                            break
                        if entry.timeslot.start_period != target:
                            if can_move_entry(entry, target):
                                entry = move_entry(entry, target)
                                pass_moves += 1
                        target += 1

        total_moves += pass_moves
        if pass_moves == 0:
            break  # No more improvements possible

    print(f"[DEFRAG] Moved {total_moves} entries to close gaps across {pass_num + 1} passes")
    return total_moves


def count_gaps_in_timetable(timetable: Timetable) -> int:
    """
    Final validation: count total number of gaps across ALL division+batch+day combos.
    A gap is any empty period between the first and last occupied period for a batch's view.
    """
    days = ["M", "T", "W", "R", "F"]
    total_gaps = 0

    div_keys = set()
    for e in timetable.entries:
        div_keys.add(e.subject.class_key)

    for div_key in div_keys:
        for day in days:
            day_entries = timetable.get_division_entries_on_day(div_key, day)
            if len(day_entries) <= 1:
                continue

            batches = set()
            for e in day_entries:
                batches.add(e.batch)
            numbered = {b for b in batches if b != "ALL"}
            check_batches = numbered if numbered else {"ALL"}

            for batch in check_batches:
                b_periods = set()
                for e in day_entries:
                    if e.batch == "ALL" or e.batch == batch:
                        b_periods.update(e.timeslot.occupied_periods)

                if len(b_periods) >= 2:
                    min_p = min(b_periods)
                    max_p = max(b_periods)
                    gaps = (max_p - min_p + 1) - len(b_periods)
                    if gaps > 0:
                        print(f"[GAP-CHECK] {div_key} day={day} batch={batch} periods={sorted(b_periods)} → {gaps} gap(s)")
                        total_gaps += gaps

    return total_gaps


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------

def run_full_schedule(
    subjects: list[Subject],
    classrooms: list[Classroom],
    fixed_slots: list[FixedSlot],
    faculty_unavailability: list[FacultyUnavailability],
    division_meta: dict[tuple[str, str, str], DivisionMeta] = None,
    practical_sessions: list[PracticalSession] = None,
    tutorial_sessions: list[PracticalSession] = None
) -> tuple[Timetable, list[Subject], list[dict]]:
    best_timetable = None
    best_unscheduled = subjects.copy()
    best_bottlenecks = []
    best_score = (float('inf'), float('inf'))  # (gaps, unscheduled) — lower is better

    for attempt in range(100):
        timetable = Timetable()
        bottlenecks = []
        
        if division_meta is None: division_meta = {}
        if practical_sessions is None: practical_sessions = []
        if tutorial_sessions is None: tutorial_sessions = []

        remaining = place_fixed_slots(timetable, fixed_slots, subjects, classrooms, division_meta)
        remaining = place_elective_groups(timetable, remaining, classrooms, faculty_unavailability, division_meta)

        unscheduled_practicals = schedule_practicals(
            timetable, practical_sessions, classrooms, faculty_unavailability, division_meta, bottlenecks
        )

        unscheduled_tutorials = schedule_tutorials(
            timetable, tutorial_sessions, classrooms, faculty_unavailability, division_meta, bottlenecks
        )

        session_subject_ids = set()
        for sess in practical_sessions + tutorial_sessions:
            for subj, _ in sess.assignments:
                session_subject_ids.add(id(subj))

        remaining_standalone = [s for s in remaining if id(s) not in session_subject_ids]
        unscheduled_theory = schedule_theory(
            timetable, remaining_standalone, classrooms, faculty_unavailability, division_meta
        )

        unscheduled = unscheduled_practicals + unscheduled_tutorials + unscheduled_theory
        
        # Find any subjects that didn't get fully scheduled
        for subj in subjects:
            needed = int(subj.hours_per_week)
            scheduled = timetable.count_subject_hours(subj)
            if scheduled < needed and subj not in unscheduled:
                print(f"[DEBUG] END-OF-ATTEMPT {attempt}: {subj.name} ({subj.subject_type}) scheduled={scheduled} < needed={needed}. Appending to unscheduled!")
                unscheduled.append(subj)

        # ---- Post-processing: defragment to close gaps ----
        defrag_timetable(timetable, faculty_unavailability)

        # Count remaining gaps after defrag
        gap_count = count_gaps_in_timetable(timetable)
        score = (gap_count, len(unscheduled))  # gaps first, then unscheduled

        print(f"[DEBUG] Attempt {attempt}: gaps={gap_count}, unscheduled={len(unscheduled)}")

        if gap_count == 0 and not unscheduled:
            # Perfect solution — zero gaps and all subjects placed
            best_timetable = timetable
            best_unscheduled = []
            best_bottlenecks = []
            best_score = (0, 0)
            print(f"[DEBUG] PERFECT solution found on attempt {attempt}!")
            break

        # Track best solution: prefer fewer gaps first, then fewer unscheduled
        if best_timetable is None or score < best_score:
            best_timetable = timetable
            best_unscheduled = unscheduled
            best_bottlenecks = bottlenecks
            best_score = score

    final_gaps = count_gaps_in_timetable(best_timetable) if best_timetable else -1
    print(f"[DEBUG] BEST ATTEMPT had {len(best_unscheduled)} unscheduled subjects, {final_gaps} gaps: {[s.name + '(' + s.subject_type + ')' for s in best_unscheduled]}")
    return best_timetable, best_unscheduled, best_bottlenecks

