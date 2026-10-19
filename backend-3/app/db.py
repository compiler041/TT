"""
db.py – SQLite persistence layer for the multi-department timetable system.

Tables:
    departments  – dept_id, password_hash, display_name, is_admin
    timetables   – id, dept_id, generated_at, status, excel_blob, admin_remark
    activity_log – id, dept_id, action, timestamp, ip_address
"""

import sqlite3
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "timetable.db"
SEED_PATH = Path(__file__).parent.parent / "departments.json"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables and seed departments if needed."""
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS departments (
        dept_id      TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL,
        is_admin     INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS timetables (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        dept_id      TEXT NOT NULL,
        generated_at TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'UNKNOWN',
        excel_blob   BLOB,
        admin_remark TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS activity_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        dept_id      TEXT NOT NULL,
        action       TEXT NOT NULL,
        timestamp    TEXT NOT NULL,
        ip_address   TEXT DEFAULT ''
    );
    """)
    conn.commit()

    # Seed departments from JSON if table is empty
    count = cur.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
    if count == 0 and SEED_PATH.exists():
        with open(SEED_PATH, "r") as f:
            depts = json.load(f)
        for d in depts:
            cur.execute(
                "INSERT OR IGNORE INTO departments (dept_id, password_hash, display_name, is_admin) VALUES (?,?,?,?)",
                (d["dept_id"], _sha256(d["password"]), d["display_name"], 1 if d.get("is_admin") else 0)
            )
        conn.commit()

    conn.close()


# ─── Department helpers ────────────────────────────────────────────────────────

def get_dept(dept_id: str) -> sqlite3.Row | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM departments WHERE dept_id = ?", (dept_id,)
    ).fetchone()
    conn.close()
    return row


def verify_password(dept_id: str, password: str) -> bool:
    row = get_dept(dept_id)
    if not row:
        return False
    return row["password_hash"] == _sha256(password)


def list_departments() -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT dept_id, display_name, is_admin FROM departments WHERE is_admin = 0"
    ).fetchall()
    conn.close()
    return rows


# ─── Timetable helpers ────────────────────────────────────────────────────────

def save_timetable(dept_id: str, status: str, excel_blob: bytes) -> int:
    """Insert a new timetable record and return its ID."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO timetables (dept_id, generated_at, status, excel_blob, admin_remark) VALUES (?,?,?,?,'')",
        (dept_id, datetime.now().isoformat(timespec="seconds"), status, excel_blob)
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_timetable(tt_id: int) -> sqlite3.Row | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT id, dept_id, generated_at, status, admin_remark FROM timetables WHERE id = ?",
        (tt_id,)
    ).fetchone()
    conn.close()
    return row


def get_timetable_blob(tt_id: int) -> bytes | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT excel_blob FROM timetables WHERE id = ?", (tt_id,)
    ).fetchone()
    conn.close()
    return bytes(row["excel_blob"]) if row and row["excel_blob"] else None


def list_timetables(dept_id: str | None = None) -> list[sqlite3.Row]:
    """List all timetables (admin) or just for one dept."""
    conn = get_conn()
    if dept_id:
        rows = conn.execute(
            "SELECT id, dept_id, generated_at, status, admin_remark FROM timetables WHERE dept_id = ? ORDER BY id DESC",
            (dept_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, dept_id, generated_at, status, admin_remark FROM timetables ORDER BY id DESC"
        ).fetchall()
    conn.close()
    return rows


def set_remark(tt_id: int, remark: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE timetables SET admin_remark = ? WHERE id = ?", (remark, tt_id)
    )
    conn.commit()
    conn.close()


def update_timetable_status(tt_id: int, status: str, excel_blob: bytes) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE timetables SET status = ?, excel_blob = ?, generated_at = ? WHERE id = ?",
        (status, excel_blob, datetime.now().isoformat(timespec="seconds"), tt_id)
    )
    conn.commit()
    conn.close()


# ─── Activity log helpers ─────────────────────────────────────────────────────

def log_action(dept_id: str, action: str, ip: str = "") -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO activity_log (dept_id, action, timestamp, ip_address) VALUES (?,?,?,?)",
        (dept_id, action, datetime.now().isoformat(timespec="seconds"), ip)
    )
    conn.commit()
    conn.close()


def get_logs(limit: int = 200) -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, dept_id, action, timestamp, ip_address FROM activity_log ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return rows
