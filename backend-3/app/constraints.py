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

def score_placement(timetable: Timetable, subject: Subject, faculty_code: str, slot: TimeSlot, classroom: Classroom = None, division_weekly_hours: dict = None) -> int:
    """Returns a penalty score (lower = better) for soft constraints.
    
    division_weekly_hours: optional dict mapping class_key -> total weekly hours
                           for the division. Used to enforce minimum daily hours.
    """
    penalty = 0
    days = ["M", "T", "W", "R", "F"]
    day_entries_div = timetable.get_division_entries_on_day(subject.class_key, slot.day)

    # --- Student used periods for this day ---
    used_periods = set()
    for e in day_entries_div:
        used_periods.update(e.timeslot.occupied_periods)

    # 1. Same Subject on Same Day — extreme penalty (+10000)
    #    Two lectures of the same subject (e.g. AEC) must NOT appear
    #    on the same day. Penalty is so high that the solver will
    #    virtually always avoid this.
    if not subject.is_practical:
        for e in day_entries_div:
            if e.subject.name == subject.name and not e.subject.is_practical:
                penalty += 5000

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

    # 4. Continuous Schedules — NO GAPS (+1000000000 Penalty per Gap period)
    #    Nuclear penalty — gaps are virtually impossible. Evaluated PER BATCH.
    batches_to_check = {"ALL"}
    if subject.batch != "ALL":
        batches_to_check.add(subject.batch)
    for e in day_entries_div:
        if e.batch != "ALL":
            batches_to_check.add(e.batch)
            
    total_gaps = 0
    for b in batches_to_check:
        b_periods = set()
        for e in day_entries_div:
            if e.batch == "ALL" or e.batch == b:
                b_periods.update(e.timeslot.occupied_periods)
        if subject.batch == "ALL" or subject.batch == b:
            b_periods.update(slot.occupied_periods)
            
        if b_periods:
            sorted_sp = sorted(b_periods)
            for i in range(1, len(sorted_sp)):
                gap = sorted_sp[i] - sorted_sp[i - 1] - 1
                if sorted_sp[i - 1] == 4 and sorted_sp[i] == 5:
                    continue  # ignore lunch break
                if gap > 0:
                    total_gaps += gap
                    
    penalty += 1000000000 * total_gaps

    # 5. Teacher Weekly Load Balancing (+200 Penalty)
    #    Penalise placing a lecture on a day where this faculty already
    #    has more hours than their average daily load, so the solver
    #    spreads the teacher's workload evenly across the week.
    if faculty_code:
        faculty_hours_by_day = {d: 0 for d in days}
        for e in timetable.entries:
            if e.faculty_code == faculty_code:
                faculty_hours_by_day[e.timeslot.day] += e.timeslot.duration
        total_existing = sum(faculty_hours_by_day.values())
        avg_daily = (total_existing + slot.duration) / 5.0
        proposed_today = faculty_hours_by_day[slot.day] + slot.duration
        if proposed_today > avg_daily + 1:
            penalty += 200 * int(proposed_today - avg_daily)

    # 6. Minimum Daily Hours — Even Distribution (+1000 Penalty)
    #    If a division has N total weekly hours, each day should get at
    #    least ceil(N/5) hours. This penalty fires when placing on a day
    #    that's already at/above the daily target while other days are
    #    still below the target. Forces the solver to fill under-loaded
    #    days first (e.g. 45 hrs/week -> at least 9 hrs/day).
    if division_weekly_hours and subject.class_key in division_weekly_hours:
        total_weekly = division_weekly_hours[subject.class_key]
        min_daily_target = math.ceil(total_weekly / len(days))

        # Count hours already scheduled per day for this division
        div_hours_by_day = {d: 0 for d in days}
        for e in timetable.entries:
            e_year, e_branch, e_div = e.subject.class_key
            q_year, q_branch, q_div = subject.class_key
            if e_year == q_year and e_div == q_div:
                if e_branch == q_branch or e_branch == "ALL" or q_branch == "ALL":
                    # Only count ALL-batch entries or unique per-batch periods
                    if e.batch == "ALL":
                        div_hours_by_day[e.timeslot.day] += e.timeslot.duration
                    else:
                        # For batch-specific entries, count them once per timeslot
                        # (multiple batches run in parallel, student only sits one)
                        pass

        # Re-count batch-specific: for each day, find unique periods occupied
        # by at least one batch entry (parallel batches share time)
        batch_periods_by_day = {d: set() for d in days}
        for e in timetable.entries:
            e_year, e_branch, e_div = e.subject.class_key
            q_year, q_branch, q_div = subject.class_key
            if e_year == q_year and e_div == q_div:
                if e_branch == q_branch or e_branch == "ALL" or q_branch == "ALL":
                    if e.batch != "ALL":
                        batch_periods_by_day[e.timeslot.day].update(e.timeslot.occupied_periods)

        for d in days:
            div_hours_by_day[d] += len(batch_periods_by_day[d])

        current_day_hours = div_hours_by_day[slot.day] + slot.duration
        days_below_target = sum(1 for d in days if div_hours_by_day[d] < min_daily_target and d != slot.day)

        # If this day is already at/above target but other days are still below,
        # heavily penalize to push placement to under-loaded days
        if div_hours_by_day[slot.day] >= min_daily_target and days_below_target > 0:
            excess = current_day_hours - min_daily_target
            penalty += 15000 * max(1, excess)

        # Lighter penalty: even if under target, prefer the day with fewest hours
        # to ensure even filling across all days
        min_hours_day = min(div_hours_by_day.values())
        if div_hours_by_day[slot.day] > min_hours_day + 1:
            penalty += 200 * (div_hours_by_day[slot.day] - min_hours_day)

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

    if subject.is_practical and slot.duration >= 2:
        if slot.start_period == 4:
            return False
        if slot.start_period >= 9:
            return False

    # =====================================================================
    # HC — Same theory subject cannot appear twice on the same day
    # =====================================================================
    if not subject.is_practical:
        day_entries = timetable.get_division_entries_on_day(subject.class_key, slot.day)
        for e in day_entries:
            if e.subject.name == subject.name and not e.subject.is_practical:
                return False

    # =====================================================================
    # HC — STRICT NO GAP (per batch, morning/afternoon blocks)
    # A placement is REJECTED if it would create a gap in any batch's
    # morning block (P1-P4) or afternoon block (P5-P9).
    # The lunch break between P4 and P5 is a natural divider —
    # having morning classes and afternoon classes is fine even if
    # there's no class right before/after lunch.
    # =====================================================================
    day_entries = timetable.get_division_entries_on_day(subject.class_key, slot.day)

    # Collect every batch that exists on this day (including the proposed one)
    batches_on_day = set()
    for e in day_entries:
        batches_on_day.add(e.batch)
    batches_on_day.add(subject.batch)

    # Normalise: if "ALL" is present, we must also check every numbered batch
    numbered_batches = {b for b in batches_on_day if b != "ALL"}
    check_batches = numbered_batches if numbered_batches else {"ALL"}

    def _has_gap_in_block(periods):
        """True if there's a gap within a set of periods."""
        if len(periods) < 2:
            return False
        min_p = min(periods)
        max_p = max(periods)
        return (max_p - min_p + 1) > len(periods)

    for b in check_batches:
        b_periods = set()
        for e in day_entries:
            if e.batch == "ALL" or e.batch == b:
                b_periods.update(e.timeslot.occupied_periods)
        if subject.batch == "ALL" or subject.batch == b:
            b_periods.update(slot.occupied_periods)

        # Split into morning and afternoon blocks
        morning = {p for p in b_periods if p <= 4}
        afternoon = {p for p in b_periods if p >= 5}

        if _has_gap_in_block(morning) or _has_gap_in_block(afternoon):
            return False   # gap detected → reject

    return (
        faculty_is_free(timetable, faculty_code, slot, faculty_unavailability)
        and class_is_free(timetable, subject.class_key, slot, subject.batch)
        and room_ok
    )