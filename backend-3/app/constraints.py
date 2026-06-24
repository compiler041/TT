"""
Constraints - the rules a placement must satisfy before it's allowed
into the Timetable.

Each function answers ONE yes/no question. They are pure (no mutation,
no side effects) - they only ever look at the current Timetable state
and return True/False.
"""

from app.models import Timetable, TimeSlot, Classroom, Subject, FacultyUnavailability
import math

def faculty_is_free(
    timetable: Timetable,
    faculty_code: str,
    slot: TimeSlot,
    faculty_unavailability: list[FacultyUnavailability],
) -> bool:
    """
    True if `faculty_code` can teach at `slot`.

    Two things disqualify a faculty:
    1. Already teaching something else that overlaps this slot
       (is_faculty_busy already uses .overlaps() internally, so a
       2-period practical correctly blocks both periods it covers).
    2. The slot overlaps a period this faculty marked unavailable.
       FacultyUnavailability only stores a single `period` (not a
       start_period+duration like TimeSlot), so we build a 1-period
       TimeSlot from it and reuse .overlaps() for the comparison -
       this way a 2-period practical attempting periods (3,4) will
       correctly be blocked if the faculty is unavailable at period 4,
       even though the unavailability record only mentions "4".
    """
    if timetable.is_faculty_busy(faculty_code, slot):
        return False

    for unavail in faculty_unavailability:
        if unavail.faculty_code != faculty_code:
            continue
        unavail_slot = TimeSlot(day=unavail.day, start_period=unavail.period)
        if slot.overlaps(unavail_slot):
            return False

    return True


def room_is_free(timetable: Timetable, room_id: str, slot: TimeSlot) -> bool:
    """
    True if `room_id` is not occupied by anyone at `slot`.

    timetable.is_room_busy() already does overlap-aware checking -
    this is a thin negation wrapper so the rest of this module reads
    consistently (every constraint here is a positive "is_X_free" check).
    """
    return not timetable.is_room_busy(room_id, slot)


def class_is_free(
    timetable: Timetable,
    class_key: tuple[str, str, str],
    slot: TimeSlot,
    batch: str,
) -> bool:
    """
    True if this year+branch+division (class_key), for this specific
    `batch`, has nothing else overlapping `slot`.

    Same pattern as room_is_free: is_class_busy already encodes the
    ALL-vs-specific-batch logic, we just negate it.
    """
    return not timetable.is_class_busy(class_key, slot, batch)


def room_type_matches(subject: Subject, classroom: Classroom) -> bool:
    """
    True if `classroom` is an acceptable room for `subject`.

    NOTE - design decision, flagged for your review:
    Since rooms are already pre-assigned per subject in the CSV
    (subject.room), the scheduler will look up the SAME classroom object
    every time for a given subject - it isn't choosing freely from all
    available rooms. That makes this function less about "search and
    filter valid rooms" and more about "sanity-check the CSV didn't
    assign something nonsensical" (e.g. a practical accidentally pointed
    at a plain classroom instead of a lab).

    Kept as a real per-placement constraint (not just a one-time load-time
    check) for one reason: it's nearly free to check (no loop, no state),
    and keeping it inside is_valid_placement means if the CSV ever assigns
    a bad room for one specific entry, the scheduler skips/flags THAT
    entry instead of crashing the whole load or silently producing a
    wrong timetable. Cost of checking 173 times is negligible either way.

    Rule:
    - subject.is_practical -> classroom MUST be a lab
    - otherwise -> classroom must NOT be a lab (don't waste scarce lab
      space on a lecture/tutorial)

    # YOUR REVIEW: confirm this matches your CSV's actual room column -
    # if subject.room is sometimes "ONLINE" for online lectures, that
    # string won't match any real Classroom.room_id at all, meaning the
    # scheduler's room lookup (in scheduler.py, not here) needs to special
    # case "ONLINE" before it ever reaches this function.
    """
    if subject.is_practical:
        return classroom.is_lab
    return not classroom.is_lab


def is_valid_placement(
    timetable: Timetable,
    subject: Subject,
    faculty_code: str,
    classroom: Classroom,
    slot: TimeSlot,
    faculty_unavailability: list[FacultyUnavailability],
) -> bool:
    """
    Master check - a placement is valid only if EVERY constraint passes.
    This is what scheduler.py actually calls; the four functions above
    are just its building blocks.

    ONLINE subjects skip room conflict and room-type checks entirely -
    there is no physical room to conflict over.
    """
    is_online = classroom.room_type.upper() == "ONLINE"

    room_ok = True if is_online else (
        room_is_free(timetable, classroom.room_id, slot)
        and room_type_matches(subject, classroom)
    )

    return (
        faculty_is_free(timetable, faculty_code, slot, faculty_unavailability)
        and class_is_free(timetable, subject.class_key, slot, subject.batch)
        and room_ok
    )

def subject_hours_not_exceeded(timetable: Timetable, subject: Subject, duration: int) -> bool:
    count = timetable.count_subject_hours(subject)
    # Allow exceeding hours_per_week up to the next full block
    max_allowed = math.ceil(subject.hours_per_week / duration) * duration
    return (count + duration) <= max_allowed

def division_max_periods_per_day(timetable: Timetable, class_key: tuple[str, str, str], day: str, duration: int) -> bool:
    """True if division won't exceed 9 teaching periods today."""
    entries = timetable.get_division_entries_on_day(class_key, day)
    used_periods = set()
    for e in entries:
        used_periods.update(e.timeslot.occupied_periods)
    return (len(used_periods) + duration) <= 9

def no_theory_during_practical(timetable: Timetable, class_key: tuple[str, str, str], slot: TimeSlot) -> bool:
    """True if no theory/tutorial is scheduled for this division at `slot`."""
    entries = timetable.get_division_entries_on_day(class_key, slot.day)
    for e in entries:
        if not e.subject.is_practical and e.timeslot.overlaps(slot):
            return False
    return True

def score_placement(timetable: Timetable, subject: Subject, faculty_code: str, slot: TimeSlot, classroom: Classroom = None) -> int:
    """Returns a penalty score (lower = better) for soft constraints."""
    penalty = 0
    days = ["M", "T", "W", "R", "F"]
    day_entries_div = timetable.get_division_entries_on_day(subject.class_key, slot.day)

    # --- Student used periods for this day ---
    used_periods = set()
    for e in day_entries_div:
        used_periods.update(e.timeslot.occupied_periods)

    # 1. Theory Spread (+10 Penalty)
    if not subject.is_practical and subject.subject_type == "L":
        for e in day_entries_div:
            if e.subject.name == subject.name and e.subject.subject_type == "L":
                penalty += 10

    # 2. Tutorial Spread (+15 Penalty)
    if subject.subject_type == "T":
        day_idx = days.index(slot.day)
        for e in timetable.entries:
            if e.subject.name == subject.name and e.subject.subject_type == "T" and e.subject.class_key == subject.class_key:
                e_idx = days.index(e.timeslot.day)
                if abs(day_idx - e_idx) <= 1:
                    penalty += 15  # same day or adjacent day — bad

    # 3. Balanced Daily Workload (+8 Penalty)
    student_total_today = len(used_periods) + slot.duration
    if student_total_today > 6:
        penalty += 8

    # 4. Continuous Schedules (+3 Penalty per Gap)
    proposed_student_periods = used_periods | set(slot.occupied_periods)
    if proposed_student_periods:
        sorted_sp = sorted(proposed_student_periods)
        for i in range(1, len(sorted_sp)):
            gap = sorted_sp[i] - sorted_sp[i - 1] - 1
            if sorted_sp[i - 1] == 4 and sorted_sp[i] == 5:
                continue  # ignore lunch break
            if gap > 0:
                penalty += 3 * gap

    return penalty


def is_valid_placement_v2(
    timetable: Timetable,
    subject: Subject,
    faculty_code: str,
    classroom: Classroom,
    slot: TimeSlot,
    faculty_unavailability: list[FacultyUnavailability],
) -> bool:
    is_online = classroom.room_type.upper() == "ONLINE"
    room_ok = True if is_online else (
        room_is_free(timetable, classroom.room_id, slot)
        and room_type_matches(subject, classroom)
    )

    if not subject_hours_not_exceeded(timetable, subject, slot.duration):
        return False
        
    if not division_max_periods_per_day(timetable, subject.class_key, slot.day, slot.duration):
        return False
        
    if subject.is_practical and not no_theory_during_practical(timetable, subject.class_key, slot):
        return False

    # =====================================================================
    # HC — Never start a practical at period 4 (11-12) or 9 (5-6)
    # =====================================================================
    if subject.is_practical and slot.duration >= 2:
        if slot.start_period == 4:
            return False
        if slot.start_period >= 9:
            return False
    return (
        faculty_is_free(timetable, faculty_code, slot, faculty_unavailability)
        and class_is_free(timetable, subject.class_key, slot, subject.batch)
        and room_ok
    )