"""
Domain models for the Timetable Scheduler.

Design notes:
- Days are single-letter codes matching your sheet: M T W R F (R = Thursday, to avoid T/T clash)
- Periods are integers 1-8. Real clock times are derived from PERIOD_TIME_MAP, never stored per-entry.
- "ALL" batch means the entry applies to the whole class at once (a lecture).
  A non-"ALL" batch number means the class was split (e.g. into 3 practical batches).
"""

from dataclasses import dataclass, field
from datetime import time


# ---------------------------------------------------------------------
# Locked period -> clock time map (8 periods, 8AM-5PM, lunch after period 4)
# ---------------------------------------------------------------------

PERIOD_TIME_MAP: dict[int, tuple[time, time]] = {
    1: (time(8, 0), time(9, 0)),
    2: (time(9, 0), time(10, 0)),
    3: (time(10, 0), time(11, 0)),
    4: (time(11, 0), time(12, 0)),
    # lunch break 12:00-13:00, period 5 resumes after
    5: (time(13, 0), time(14, 0)),
    6: (time(14, 0), time(15, 0)),
    7: (time(15, 0), time(16, 0)),
    8: (time(16, 0), time(17, 0)),
    9: (time(17, 0), time(18, 0)),  # overflow period, used only when 1-8 are full
}

VALID_DAYS = ("M", "T", "W", "R", "F")  # Mon Tue Wed Thu Fri
VALID_PERIODS = tuple(PERIOD_TIME_MAP.keys())


# ---------------------------------------------------------------------
# Core entities
# ---------------------------------------------------------------------

@dataclass
class Subject:
    """One row of the BASE SHEET, *before* batch-splitting."""
    year: str                 # SY, TY, LY
    semester: float           # 3.0, 5.0, 7.0
    branch: str                # EXCP, CSBS, VLSI, ALL
    division: str               # A
    batch: str                  # "ALL" or a number as string ("1","2","3","4")
    course_type: str            # C, E, O, M, MDM, E1, E2 ...
    name: str                   # DSA, ML, COA ...
    faculty_codes: list[str]    # one code per batch, parsed from "RTD,AYH,AYH"
    room: str                   # B405, CR, ONLINE ...
    subject_type: str           # L (lecture), T (tutorial), P (practical)
    hours_per_week: float

    @property
    def is_practical(self) -> bool:
        return self.subject_type == "P"

    @property
    def duration(self) -> int:
        """Practicals occupy 2 consecutive periods; lectures/tutorials occupy 1."""
        return 2 if self.is_practical else 1

    @property
    def class_key(self) -> tuple[str, str, str]:
        """Identifies a unique student group: year+branch+division."""
        return (self.year, self.branch, self.division)


@dataclass
class DivisionMeta:
    class_key: tuple[str, str, str]  # (year, branch, division)
    batch_count: int                 # max batch count across all subjects
    theory_room: str                 # fixed classroom (e.g., "B405")


@dataclass
class PracticalSession:
    division_key: tuple[str, str, str]
    assignments: list[tuple[Subject, str]]
    # Each tuple: (Subject entry for specific batch, faculty_code)
    # Length = batch_count. One entry per batch, all scheduled at same timeslot.


@dataclass
class Classroom:
    room_id: str
    room_type: str   # "CR" (classroom) or "LAB"

    @property
    def is_lab(self) -> bool:
        return self.room_type.upper() == "LAB"


@dataclass
class FixedSlot:
    """A pre-locked slot from MDM COURSES sheet. Placed before anything else."""
    year: str
    course_type: str
    semester: float
    day: str          # M T W R F
    period: int        # 1-8


@dataclass
class FacultyUnavailability:
    """One faculty cannot teach at this day+period."""
    faculty_code: str
    day: str
    period: int


@dataclass(frozen=True)
class TimeSlot:
    """
    A block of time on the timetable.

    duration=1 -> lecture/tutorial (1 period)
    duration=2 -> practical (2 consecutive periods)

    Two TimeSlots conflict if they're on the same day AND share at least
    one period - use .overlaps() for this, never == (equality only
    catches identical start_period, missing partial overlaps).
    """
    day: str
    start_period: int
    duration: int = 1

    def __post_init__(self):
        if self.day not in VALID_DAYS:
            raise ValueError(f"Invalid day: {self.day}")
        if self.start_period not in VALID_PERIODS:
            raise ValueError(f"Invalid start period: {self.start_period}")
        if self.duration < 1:
            raise ValueError("Duration must be at least 1")
        end_period = self.start_period + self.duration - 1
        if end_period not in VALID_PERIODS:
            raise ValueError(
                f"Timeslot exceeds timetable bounds ({self.start_period} -> {end_period})"
            )

    @property
    def occupied_periods(self) -> set[int]:
        """All period numbers this slot covers, e.g. {3, 4} for a 2-period practical starting at 3."""
        return set(range(self.start_period, self.start_period + self.duration))

    @property
    def start_time(self) -> time:
        return PERIOD_TIME_MAP[self.start_period][0]

    @property
    def end_time(self) -> time:
        end_period = self.start_period + self.duration - 1
        return PERIOD_TIME_MAP[end_period][1]

    def overlaps(self, other: "TimeSlot") -> bool:
        """True if both slots are on the same day and share at least one period."""
        if self.day != other.day:
            return False
        return bool(self.occupied_periods & other.occupied_periods)


@dataclass
class ScheduleEntry:
    """One placed block in the final timetable."""
    subject: Subject
    faculty_code: str
    classroom: Classroom
    timeslot: TimeSlot
    batch: str          # "ALL" or batch number, copied from Subject for convenience


@dataclass
class Timetable:
    entries: list[ScheduleEntry] = field(default_factory=list)

    def add(self, entry: ScheduleEntry) -> None:
        self.entries.append(entry)

    def is_faculty_busy(self, faculty_code: str, slot: TimeSlot) -> bool:
        return any(
            e.faculty_code == faculty_code and e.timeslot.overlaps(slot)
            for e in self.entries
        )

    def is_room_busy(self, room_id: str, slot: TimeSlot) -> bool:
        return any(
            e.classroom.room_id == room_id and e.timeslot.overlaps(slot)
            for e in self.entries
        )

    def is_class_busy(self, class_key: tuple[str, str, str], slot: TimeSlot, batch: str) -> bool:
        """
        A class group is busy at a slot if:
        - an "ALL"-batch entry already overlaps that slot for this class, OR
        - this specific batch already has an overlapping entry, OR
        - we're trying to place an "ALL" entry but some batch already overlaps there.
        """
        for e in self.entries:
            if not e.timeslot.overlaps(slot):
                continue
                
            e_year, e_branch, e_div = e.subject.class_key
            q_year, q_branch, q_div = class_key
            
            if e_year != q_year or e_div != q_div:
                continue
                
            # If branches match, or one of them is "ALL", it's a conflict
            if e_branch == q_branch or e_branch == "ALL" or q_branch == "ALL":
                if e.batch == "ALL" or batch == "ALL" or e.batch == batch:
                    return True
        return False

    def get_by_class(self, class_key: tuple[str, str, str]) -> list[ScheduleEntry]:
        year, branch, div = class_key
        return [e for e in self.entries if e.subject.class_key == class_key or (e.subject.year == year and e.subject.division == div and (e.subject.branch == "ALL" or branch == "ALL"))]

    def get_by_faculty(self, faculty_code: str) -> list[ScheduleEntry]:
        return [e for e in self.entries if e.faculty_code == faculty_code]

    def get_by_room(self, room_id: str) -> list[ScheduleEntry]:
        return [e for e in self.entries if e.classroom.room_id == room_id]

    def count_subject_hours(self, subject: Subject) -> int:
        """Counts how many hours have been scheduled for a specific subject+batch+class_key."""
        count = 0
        for e in self.entries:
            if (e.subject.name == subject.name and 
                e.subject.subject_type == subject.subject_type and
                e.subject.class_key == subject.class_key and 
                e.batch == subject.batch):
                count += e.timeslot.duration
        return count

    def get_division_entries_on_day(self, class_key: tuple[str, str, str], day: str) -> list[ScheduleEntry]:
        """Returns all entries for a division on a specific day."""
        year, branch, div = class_key
        return [e for e in self.entries if e.timeslot.day == day and (e.subject.class_key == class_key or (e.subject.year == year and e.subject.division == div and (e.subject.branch == "ALL" or branch == "ALL")))]