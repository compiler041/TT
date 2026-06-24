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

    for fs in fixed_slots:
        slot = TimeSlot(day=fs.day, start_period=fs.period, duration=1)

        matches = [
            s for s in subjects
            if s.year == fs.year
            and s.course_type == fs.course_type
            and s.semester == fs.semester
            and (s.subject_type == "L" or s.subject_type == "P") # MDM can be L or P
        ]

        if not matches:
            continue

        for subj in matches:
            faculty_code = subj.faculty_codes[0] if subj.faculty_codes else ""
            classroom = resolve_classroom(subj, classrooms, timetable, slot, division_meta)

            if classroom is None:
                continue

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
    
    # Pre-calculate total theory hours needed per division
    from collections import defaultdict
    div_theory_hours = defaultdict(int)
    for s in subjects:
        div_theory_hours[s.class_key] += s.hours_per_week

    def get_faculty_constraint_score(s: Subject) -> int:
        count = 0
        for fcode in s.faculty_codes:
            # Add explicit unavailability (from NA sheet)
            for u in faculty_unavailability:
                if u.faculty_code == fcode:
                    count += 5 # Weight NA higher
            
            # Add current booked hours from timetable (from practicals/MDM/other divisions)
            for e in timetable.entries:
                if e.faculty_code == fcode:
                    count += e.timeslot.duration
        return count

    # Sort primarily by how tight the division's schedule is, then subject length, then faculty constraints
    sorted_subjects = sorted(subjects, key=lambda s: (
        -div_theory_hours[s.class_key],
        -s.hours_per_week, 
        -get_faculty_constraint_score(s)
    ))

    import math
    for subj in sorted_subjects:
        faculty_code = subj.faculty_codes[0] if subj.faculty_codes else ""
        hours_missing = int(subj.hours_per_week) - timetable.count_subject_hours(subj)
        if hours_missing <= 0: continue

        blocks_needed = math.ceil(hours_missing / subj.duration)
        for _ in range(int(blocks_needed)):
            placed_this = False
            candidate_slots = []

            for slot in all_slots(duration=subj.duration):
                classroom = resolve_classroom(subj, classrooms, timetable, slot, division_meta)
                if classroom is None:
                    continue

                if is_valid_placement_v2(
                    timetable, subj, faculty_code, classroom, slot, faculty_unavailability
                ):
                    score = score_placement(timetable, subj, faculty_code, slot, classroom)
                    candidate_slots.append((score, slot, classroom))

            if candidate_slots:
                random.shuffle(candidate_slots)
                candidate_slots.sort(key=lambda x: x[0])
                _, best_slot, best_classroom = candidate_slots[0]
                
                timetable.add(ScheduleEntry(
                    subject=subj,
                    faculty_code=faculty_code,
                    classroom=best_classroom,
                    timeslot=best_slot,
                    batch=subj.batch,
                ))
                placed_this = True
            else:
                print(f"[DEBUG] Core Theory {subj.name} ({subj.branch}) FAILED TO PLACE! hours_per_week={subj.hours_per_week}, room={subj.room}, faculty={subj.faculty_codes}")

            if not placed_this:
                unscheduled.append(subj)
                break

    return unscheduled

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

    for attempt in range(15):
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

        if not unscheduled:
            best_timetable = timetable
            best_unscheduled = []
            best_bottlenecks = []
            break
        else:
            if len(unscheduled) < len(best_unscheduled):
                best_timetable = timetable
                best_unscheduled = unscheduled
                best_bottlenecks = bottlenecks

    print(f"[DEBUG] BEST ATTEMPT had {len(best_unscheduled)} unscheduled subjects: {[s.name + '(' + s.subject_type + ')' for s in best_unscheduled]}")
    return best_timetable, best_unscheduled, best_bottlenecks
