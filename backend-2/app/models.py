"""
Pydantic models for the Timetable Maker backend.
"""
from __future__ import annotations
from typing import Optional, List, Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Master data row (from "BASE SHEET" / CSV1: YEAR & SUBJECT)
# ---------------------------------------------------------------------------
class CourseRow(BaseModel):
    """One row of the master subject/faculty CSV (BASE SHEET)."""
    class_: str = Field(alias="CLASS")          # SY / TY / LY
    sem: float = Field(alias="SEM")             # 3 / 5 / 7
    branch: str = Field(alias="BRANCH")         # EXCP / CSBS / VLSI / ALL
    division: str = Field(alias="DIVISION")     # A / B ...
    no_of_batch: str = Field(alias="NO OF BATCH")  # 'ALL' or numeric batch count for that row
    type_of_course: str = Field(alias="TYPE OF COURSE")  # C / E / E1 / E2 / M / MDM / O
    course_name: str = Field(alias="COURSE_NAME")
    faculty_name: Optional[str] = Field(default=None, alias="FACULTY NAME")
    room_no: Optional[str] = Field(default=None, alias="ROOM NO")
    type_of_subject: str = Field(alias="TYPE OF SUBJECT")  # L / T / P
    no_of_hours: float = Field(alias="NO.OF HOURS")

    class Config:
        populate_by_name = True


# ---------------------------------------------------------------------------
# Room details (CSV: ROOM DETAILS)
# ---------------------------------------------------------------------------
class RoomRow(BaseModel):
    room: str = Field(alias="ROOM")
    type: Literal["CR", "LAB"] = Field(alias="TYPE")

    class Config:
        populate_by_name = True


# ---------------------------------------------------------------------------
# MDM / fixed-time courses (CSV3: MDM COURSES)
# ---------------------------------------------------------------------------
class FixedSlotRow(BaseModel):
    class_: str = Field(alias="CLASS")
    type_of_course: str = Field(alias="TYPE OF COURSE")
    sem: float = Field(alias="SEM")
    day: str = Field(alias="DAY")      # M T W R F
    slot: str = Field(alias="TIME")    # "9-10" style string, e.g. "9-10"

    class Config:
        populate_by_name = True


# ---------------------------------------------------------------------------
# Faculty occupied / not-available slots (CSV2: FACULTY OCCUPIED)
# ---------------------------------------------------------------------------
class FacultyBusyRow(BaseModel):
    faculty_name: str = Field(alias="FACULTY NAME")
    day: str = Field(alias="DAY")
    slot: str = Field(alias="TIME")

    class Config:
        populate_by_name = True


# ---------------------------------------------------------------------------
# Generated timetable entry (output)
# ---------------------------------------------------------------------------
class TimetableEntry(BaseModel):
    class_: str
    sem: float
    branch: str
    division: str
    batch: Optional[str] = None       # None for theory (whole division)
    day: str
    slot: str
    period_index: int
    course_name: str
    type_of_subject: str              # L / T / P
    faculty: Optional[str] = None
    room: Optional[str] = None
    is_elective: bool = False
    elective_group: Optional[str] = None


class GenerationRequest(BaseModel):
    days: List[str] = ["M", "T", "W", "R", "F"]
    periods_per_day: int = 8
    lunch_period_index: int = 4   # 0-indexed period that is lunch (1pm-2pm)
    classes: Optional[List[str]] = None     # filter: only generate for these CLASS values (SY/TY/LY)
    branches: Optional[List[str]] = None    # filter on BRANCH


class GenerationResult(BaseModel):
    status: Literal["success", "partial", "failed"]
    message: str
    bottlenecks: List[str] = []
    timetable: List[TimetableEntry] = []
