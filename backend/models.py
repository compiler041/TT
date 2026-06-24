"""
Core domain models for the timetable generator.
All bitmask operations use a 45-bit integer (5 days × 9 periods).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ════════════════════════════════════════════════════════════
#  ENUMS
# ════════════════════════════════════════════════════════════

class SubjectType(str, Enum):
    THEORY    = "THEORY"
    PRACTICAL = "PRACTICAL"
    TUTORIAL  = "TUTORIAL"


# ════════════════════════════════════════════════════════════
#  SLOT ADDRESSING
# ════════════════════════════════════════════════════════════

DAYS: list[str]     = ["MON", "TUE", "WED", "THU", "FRI"]
PERIODS_PER_DAY: int = 9        # P1=9-10 … P9=5-6pm
LUNCH_PERIOD: int    = 5        # 1-indexed  (1 pm – 2 pm)
TOTAL_SLOTS: int     = 45       # 5 × 9

PERIOD_LABELS: list[str] = [
    "9-10", "10-11", "11-12", "12-1",
    "1-2 (Lunch)", "2-3", "3-4", "4-5", "5-6",
]

# Pre-compute lunch slot ids (one per day)
LUNCH_SLOTS: set[int] = {
    d * PERIODS_PER_DAY + (LUNCH_PERIOD - 1) for d in range(len(DAYS))
}


def slot_id(day: str, period: int) -> int:
    """(day string, 1-indexed period) → integer 0..44"""
    return DAYS.index(day) * PERIODS_PER_DAY + (period - 1)


def slot_to_day_period(sid: int) -> tuple[str, int]:
    """Integer 0..44 → (day string, 1-indexed period)"""
    d, p = divmod(sid, PERIODS_PER_DAY)
    return DAYS[d], p + 1


def is_free(mask: int, sid: int) -> bool:
    return (mask >> sid) & 1 == 0


def mark_busy(mask: int, sid: int) -> int:
    return mask | (1 << sid)


def consecutive_slot_ids(day: str, start_period: int, length: int) -> list[int]:
    return [slot_id(day, start_period + i) for i in range(length)]


# ════════════════════════════════════════════════════════════
#  CORE ENTITIES
# ════════════════════════════════════════════════════════════

@dataclass
class Room:
    room_no:    str
    room_type:  str                      # "CLASSROOM" | "LAB"
    lab_type:   Optional[str] = None
    capacity:   int = 60
    occupied_mask: int = field(default=0, repr=False)

    def is_free_at(self, sid: int) -> bool:
        return is_free(self.occupied_mask, sid)


@dataclass
class Course:
    code:           str
    subject_type:   SubjectType
    L:              int          # theory hours / week
    T:              int          # tutorial hours / week
    P:              int          # practical hours / week
    hours_per_week: int
    is_elective:    bool
    course_type:    str = "C"    # C, E, E1, E2, O, M, MDM
    lab_type:       Optional[str] = None


@dataclass
class Faculty:
    fac_id:         str
    eligible_codes: list[str] = field(default_factory=list)
    blocked_mask:   int = field(default=0, repr=False)
    placed_mask:    int = field(default=0, repr=False)

    def is_free_at(self, sid: int) -> bool:
        return is_free(self.blocked_mask | self.placed_mask, sid)

    def hours_placed(self) -> int:
        return bin(self.placed_mask).count("1")


@dataclass
class Batch:
    batch_id:     str
    division_key: str
    placed_mask:  int = field(default=0, repr=False)

    def is_free_at(self, sid: int) -> bool:
        return is_free(self.placed_mask, sid)


@dataclass
class Division:
    year:        str
    branch:      str
    div:         str
    sem:         int
    batches:     list[Batch] = field(default_factory=list)
    theory_room: Optional[str] = None
    placed_mask: int = field(default=0, repr=False)
    blocked_mask: int = field(default=0, repr=False)

    @property
    def key(self) -> str:
        return f"{self.year}_{self.branch}_{self.div}"

    def is_free_at(self, sid: int) -> bool:
        return is_free(self.blocked_mask | self.placed_mask, sid)

    def num_batches(self) -> int:
        return len(self.batches)


@dataclass
class FixedSlotBlock:
    year:        str
    sem:         int
    course_type: str
    slot_ids:    list[int]


# ════════════════════════════════════════════════════════════
#  SESSION — single schedulable teaching event
# ════════════════════════════════════════════════════════════

@dataclass
class Session:
    """
    One atomic teaching event the solver must place.
    Theory:    duration=1, batch_id="ALL"
    Practical: duration=2 (or more), batch_id="P1"/"P2"/…
    """
    session_id:     int
    division_key:   str
    course_code:    str
    subject_type:   SubjectType
    batch_id:       str           # "ALL" for theory, else batch label
    fac_id:         str
    room_no:        str           # "-" means solver-assigned / no room
    duration:       int           # 1 or 2 periods
    session_index:  int = 0       # e.g. PPL has 2 sessions → index 0, 1
    elective_group: str = ""      # "E", "E1", "E2" — for syncing elective slots
    course_type:    str = "C"     # C, E, E1, E2, O, M, MDM


# ════════════════════════════════════════════════════════════
#  ASSIGNMENT RECORD — solver output
# ════════════════════════════════════════════════════════════

@dataclass
class AssignmentRecord:
    slot_id:            int
    duration:           int
    division_key:       str
    batch_ids:          list[str]
    course_code:        str
    subject_type:       SubjectType
    fac_ids:            list[str]
    room_nos:           list[str]
    is_practical_group: bool
    soft_score:         float = 0.0


# ════════════════════════════════════════════════════════════
#  TIMETABLE INDEX
# ════════════════════════════════════════════════════════════

class TimetableIndex:
    def __init__(self):
        self.records:         list[AssignmentRecord]                = []
        self.division_index:  dict[str, dict[int, list[AssignmentRecord]]] = {}
        self.faculty_index:   dict[str, dict[int, list[AssignmentRecord]]] = {}
        self.room_index:      dict[str, dict[int, list[AssignmentRecord]]] = {}
        self.conflict_log:    dict[str, list[str]]                 = {}

    def add(self, rec: AssignmentRecord) -> None:
        self.records.append(rec)
        for offset in range(rec.duration):
            sid = rec.slot_id + offset
            
            div_dict = self.division_index.setdefault(rec.division_key, {})
            div_dict.setdefault(sid, []).append(rec)
            
            for fid in rec.fac_ids:
                if fid and fid != "-":
                    fac_dict = self.faculty_index.setdefault(fid, {})
                    fac_dict.setdefault(sid, []).append(rec)
                
            for rno in rec.room_nos:
                if rno and rno != "-" and rno != "CR" and rno != "ONLINE":
                    room_dict = self.room_index.setdefault(rno, {})
                    room_dict.setdefault(sid, []).append(rec)

    def log_conflict(self, category: str, reason: str) -> None:
        self.conflict_log.setdefault(category, []).append(reason)

    def get_division_week(self, div_key: str) -> dict[int, list[AssignmentRecord]]:
        return self.division_index.get(div_key, {})

    def get_faculty_week(self, fac_id: str) -> dict[int, list[AssignmentRecord]]:
        return self.faculty_index.get(fac_id, {})

    def get_room_week(self, room_no: str) -> dict[int, list[AssignmentRecord]]:
        return self.room_index.get(room_no, {})


# ════════════════════════════════════════════════════════════
#  SCHEDULER INPUT
# ════════════════════════════════════════════════════════════

@dataclass
class SchedulerInput:
    divisions:    dict[str, Division]
    courses:      dict[str, Course]
    faculty:      dict[str, Faculty]
    rooms:        dict[str, Room]
    assignments:  dict[str, dict[str, list[str]]]
    fixed_blocks: list[FixedSlotBlock] = field(default_factory=list)
    sessions:     list[Session] = field(default_factory=list)
