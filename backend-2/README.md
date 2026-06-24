# TT Maker — FastAPI Backend

Generates a college timetable from your master data using Google OR-Tools
CP-SAT, implementing the constraints in `constraints_for_tt.docx`.

## Setup

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Docs: http://localhost:8000/docs

## Input format

Upload the combined workbook (same layout as `TT_format.xlsx`) with sheets:

- **BASE SHEET** (CSV1 — YEAR & SUBJECT): CLASS, SEM, BRANCH, DIVISION,
  NO OF BATCH, TYPE OF COURSE, COURSE_NAME, FACULTY NAME, ROOM NO,
  TYPE OF SUBJECT, NO.OF HOURS
- **ROOM DETAILS**: ROOM, TYPE (CR / LAB)
- **MDM COURSES** (CSV3 — fixed time slots): CLASS, TYPE OF COURSE, SEM,
  DAY, TIME
- **FACULTY NA** (CSV2 — faculty occupied slots): FACULTY NAME, DAY, TIME

`POST /upload` with the xlsx file, or `POST /upload/csv` with separate CSVs.

### FACULTY NAME column conventions

- Single value (`VEK`) → theory faculty.
- Comma-separated list for practicals (`RTD,AYH,ATH`) → one faculty PER
  BATCH, in order. **Each batch needs a DIFFERENT faculty** (HC-T7) — the
  engine validates this before solving and reports conflicts by name.

## Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/upload` | Upload combined xlsx |
| POST | `/upload/csv` | Upload separate CSVs |
| GET  | `/data/summary` | List divisions, courses, rooms loaded |
| POST | `/generate?time_limit_seconds=60` | Run solver, returns full timetable |
| GET  | `/timetable` | Last generated timetable |
| GET  | `/timetable/division?class=SY&sem=3&branch=EXCP&division=A` | Filter by division |
| GET  | `/timetable/export.csv` | Download as CSV |

## `/generate` body

```json
{
  "days": ["M","T","W","R","F"],
  "classes": ["SY"],
  "branches": ["EXCP"]
}
```

Both `classes` and `branches` are optional filters (omit to schedule
everything in one solve).

## Constraint coverage

- **HC-H1/H2/H3** — theory = whole division/one room/one teacher; practicals
  split into batches, all batches of a division scheduled in the same slot,
  each batch in its own lab.
- **HC-T1/T2/T7** — no teacher double-booked; each practical batch gets its
  own free teacher.
- **HC-T5/S1-S3** — exact weekly hours per L/T/P.
- **HC-S4** — practical hours scheduled as consecutive 2-period blocks.
- **HC-S6** — L/T/P of the same subject never share a slot.
- **HC-R1-R4/R7** — room-type correctness, no double-booking, sufficient
  labs for all simultaneous batches.
- **HC-D1-D4** — no division/batch double-booking; 1pm-2pm lunch blocked for
  everyone.
- **HC-H7** — fixed slots from `MDM COURSES` are honored when present.
- **PS-1..PS-5** — practicals scheduled atomically; on infeasibility, the
  `/generate` response's `bottlenecks` array names the specific issue
  (e.g. lab shortage, faculty conflict, unavailable faculty) instead of
  failing silently.
- **SC-S1 / SC-Q3** — soft objective penalizes repeating the same
  subject/type on the same day more than once.

## Known data-quality checks

Before solving, the engine validates that practical FACULTY lists don't
reuse the same teacher across two batches that run simultaneously. If they
do, `/generate` returns `status: "failed"` with a clear per-course message
instead of an opaque solver timeout — fix the `FACULTY NAME` column in
BASE SHEET and re-upload.
